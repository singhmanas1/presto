/*
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.facebook.presto.hive.metastore.unity;

import com.facebook.airlift.log.Logger;
import com.facebook.airlift.units.Duration;
import com.facebook.presto.common.predicate.Domain;
import com.facebook.presto.common.type.Type;
import com.facebook.presto.hive.HiveStorageFormat;
import com.facebook.presto.hive.HiveType;
import com.facebook.presto.hive.PartitionNameWithVersion;
import com.facebook.presto.hive.metastore.Column;
import com.facebook.presto.hive.metastore.Database;
import com.facebook.presto.hive.metastore.ExtendedHiveMetastore;
import com.facebook.presto.hive.metastore.HivePrivilegeInfo;
import com.facebook.presto.hive.metastore.MetastoreContext;
import com.facebook.presto.hive.metastore.MetastoreOperationResult;
import com.facebook.presto.hive.metastore.MetastoreUtil;
import com.facebook.presto.hive.metastore.Partition;
import com.facebook.presto.hive.metastore.PartitionStatistics;
import com.facebook.presto.hive.metastore.PartitionWithStatistics;
import com.facebook.presto.hive.metastore.PrincipalPrivileges;
import com.facebook.presto.hive.metastore.StorageFormat;
import com.facebook.presto.hive.metastore.Table;
import com.facebook.presto.spi.PrestoException;
import com.facebook.presto.spi.constraints.TableConstraint;
import com.facebook.presto.spi.security.PrestoPrincipal;
import com.facebook.presto.spi.security.PrincipalType;
import com.facebook.presto.spi.security.RoleGrant;
import com.facebook.presto.spi.statistics.ColumnStatisticType;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.inject.Inject;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Supplier;

import static com.facebook.presto.hive.HiveErrorCode.HIVE_METASTORE_ERROR;
import static com.facebook.presto.hive.metastore.PrestoTableType.MANAGED_TABLE;
import static java.util.Objects.requireNonNull;

/**
 * Resolves a Unity Catalog table to the storage location Unity Catalog
 * assigned, then hands that location to the existing Delta reader.
 */
public class UnityHiveMetastore
        implements ExtendedHiveMetastore
{
    private static final Logger log = Logger.get(UnityHiveMetastore.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final String server;
    private final String catalog;
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(java.time.Duration.ofSeconds(10))
            .build();

    @Inject
    public UnityHiveMetastore(UnityMetastoreConfig config)
    {
        this.server = requireNonNull(config.getUri(), "uri is null").replaceAll("/+$", "");
        this.catalog = requireNonNull(config.getCatalog(), "catalog is null");
    }

    @Override
    public Optional<Database> getDatabase(MetastoreContext metastoreContext, String databaseName)
    {
        if (!getAllDatabases(metastoreContext).contains(databaseName)) {
            return Optional.empty();
        }
        return Optional.of(Database.builder()
                .setDatabaseName(databaseName)
                .setOwnerName("presto")
                .setOwnerType(PrincipalType.USER)
                .build());
    }

    @Override
    public List<String> getAllDatabases(MetastoreContext metastoreContext)
    {
        JsonNode root = getJson("/api/2.1/unity-catalog/schemas?catalog_name=" + catalog);
        JsonNode schemas = root.get("schemas");
        List<String> names = new ArrayList<>();
        if (schemas != null && schemas.isArray()) {
            for (JsonNode schema : schemas) {
                if (schema.hasNonNull("name")) {
                    names.add(schema.get("name").asText());
                }
            }
        }
        return names;
    }

    @Override
    public Optional<Table> getTable(MetastoreContext metastoreContext, String databaseName, String tableName)
    {
        String fullName = catalog + "." + databaseName + "." + tableName;
        JsonNode root = getJson("/api/2.1/unity-catalog/tables/" + fullName);
        JsonNode locationNode = root.get("storage_location");
        if (locationNode == null || locationNode.asText().isEmpty()) {
            throw new PrestoException(
                    HIVE_METASTORE_ERROR,
                    "Unity Catalog table " + fullName + " returned no storage_location: " + root);
        }
        String location = locationNode.asText();
        log.info("Unity Catalog %s storage_location=%s", fullName, location);
        return Optional.of(Table.builder()
                .setDatabaseName(databaseName)
                .setTableName(tableName)
                .setOwner("presto")
                .setTableType(MANAGED_TABLE)
                .withStorage(storage -> storage
                        .setStorageFormat(StorageFormat.fromHiveStorageFormat(HiveStorageFormat.PARQUET))
                        .setLocation(location))
                .build());
    }

    @Override
    public Set<ColumnStatisticType> getSupportedColumnStatistics(MetastoreContext metastoreContext, Type type)
    {
        return MetastoreUtil.getSupportedColumnStatistics(type);
    }

    @Override
    public PartitionStatistics getTableStatistics(MetastoreContext metastoreContext, String databaseName, String tableName)
    {
        return PartitionStatistics.empty();
    }

    @Override
    public Map<String, PartitionStatistics> getPartitionStatistics(MetastoreContext metastoreContext, String databaseName, String tableName, Set<String> partitionNames)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void updateTableStatistics(MetastoreContext metastoreContext, String databaseName, String tableName, Function<PartitionStatistics, PartitionStatistics> update)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void updatePartitionStatistics(MetastoreContext metastoreContext, String databaseName, String tableName, Map<String, Function<PartitionStatistics, PartitionStatistics>> updates)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public Optional<List<String>> getAllTables(MetastoreContext metastoreContext, String databaseName)
    {
        JsonNode root = getJson("/api/2.1/unity-catalog/tables?catalog_name=" + catalog + "&schema_name=" + databaseName);
        JsonNode tables = root.get("tables");
        List<String> names = new ArrayList<>();
        if (tables != null && tables.isArray()) {
            for (JsonNode table : tables) {
                if (table.hasNonNull("name")) {
                    names.add(table.get("name").asText());
                }
            }
        }
        return Optional.of(names);
    }

    @Override
    public Optional<List<String>> getAllViews(MetastoreContext metastoreContext, String databaseName)
    {
        return Optional.of(List.of());
    }

    @Override
    public void createDatabase(MetastoreContext metastoreContext, Database database)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void dropDatabase(MetastoreContext metastoreContext, String databaseName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void renameDatabase(MetastoreContext metastoreContext, String databaseName, String newDatabaseName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult createTable(MetastoreContext metastoreContext, Table table, PrincipalPrivileges principalPrivileges, List<TableConstraint<String>> constraints)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void dropTable(MetastoreContext metastoreContext, String databaseName, String tableName, boolean deleteData)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult replaceTable(MetastoreContext metastoreContext, String databaseName, String tableName, Table newTable, PrincipalPrivileges principalPrivileges)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult persistTable(MetastoreContext metastoreContext, String databaseName, String tableName, Table newTable, PrincipalPrivileges principalPrivileges, Supplier<PartitionStatistics> update, Map<String, String> additionalParameters)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult renameTable(MetastoreContext metastoreContext, String databaseName, String tableName, String newDatabaseName, String newTableName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult addColumn(MetastoreContext metastoreContext, String databaseName, String tableName, String columnName, HiveType columnType, String columnComment)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult renameColumn(MetastoreContext metastoreContext, String databaseName, String tableName, String oldColumnName, String newColumnName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult dropColumn(MetastoreContext metastoreContext, String databaseName, String tableName, String columnName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public Optional<Partition> getPartition(MetastoreContext metastoreContext, String databaseName, String tableName, List<String> partitionValues)
    {
        return Optional.empty();
    }

    @Override
    public Optional<List<PartitionNameWithVersion>> getPartitionNames(MetastoreContext metastoreContext, String databaseName, String tableName)
    {
        return Optional.of(List.of());
    }

    @Override
    public List<PartitionNameWithVersion> getPartitionNamesByFilter(MetastoreContext metastoreContext, String databaseName, String tableName, Map<Column, Domain> partitionPredicates)
    {
        return List.of();
    }

    @Override
    public List<PartitionNameWithVersion> getPartitionNamesWithVersionByFilter(MetastoreContext metastoreContext, String databaseName, String tableName, Map<Column, Domain> partitionPredicates)
    {
        return List.of();
    }

    @Override
    public Map<String, Optional<Partition>> getPartitionsByNames(MetastoreContext metastoreContext, String databaseName, String tableName, List<PartitionNameWithVersion> partitionNames)
    {
        return Map.of();
    }

    @Override
    public MetastoreOperationResult addPartitions(MetastoreContext metastoreContext, String databaseName, String tableName, List<PartitionWithStatistics> partitions)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void dropPartition(MetastoreContext metastoreContext, String databaseName, String tableName, List<String> parts, boolean deleteData)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult alterPartition(MetastoreContext metastoreContext, String databaseName, String tableName, PartitionWithStatistics partition)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void createRole(MetastoreContext metastoreContext, String role, String grantor)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void dropRole(MetastoreContext metastoreContext, String role)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public Set<String> listRoles(MetastoreContext metastoreContext)
    {
        return Set.of();
    }

    @Override
    public void grantTablePrivileges(MetastoreContext metastoreContext, String databaseName, String tableName, PrestoPrincipal grantee, Set<HivePrivilegeInfo> privileges)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void revokeTablePrivileges(MetastoreContext metastoreContext, String databaseName, String tableName, PrestoPrincipal grantee, Set<HivePrivilegeInfo> privileges)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public Set<HivePrivilegeInfo> listTablePrivileges(MetastoreContext metastoreContext, String databaseName, String tableName, PrestoPrincipal principal)
    {
        return Set.of();
    }

    @Override
    public void grantRoles(MetastoreContext metastoreContext, Set<String> roles, Set<PrestoPrincipal> grantees, boolean withAdminOption, PrestoPrincipal grantor)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public void revokeRoles(MetastoreContext metastoreContext, Set<String> roles, Set<PrestoPrincipal> grantees, boolean adminOptionFor, PrestoPrincipal grantor)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public Set<RoleGrant> listRoleGrants(MetastoreContext metastoreContext, PrestoPrincipal principal)
    {
        return Set.of();
    }

    @Override
    public void setPartitionLeases(MetastoreContext metastoreContext, String databaseName, String tableName, Map<String, String> partitionNameToLocation, Duration leaseDuration)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult dropConstraint(MetastoreContext metastoreContext, String databaseName, String tableName, String constraintName)
    {
        throw new UnsupportedOperationException();
    }

    @Override
    public MetastoreOperationResult addConstraint(MetastoreContext metastoreContext, String databaseName, String tableName, TableConstraint<String> tableConstraint)
    {
        throw new UnsupportedOperationException();
    }

    private JsonNode getJson(String path)
    {
        String url = server + path;
        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                    .timeout(java.time.Duration.ofSeconds(30))
                    .header("Accept", "application/json")
                    .GET()
                    .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            String body = response.body() == null ? "" : response.body();
            if (response.statusCode() / 100 != 2) {
                throw new PrestoException(
                        HIVE_METASTORE_ERROR,
                        "Unity Catalog GET " + url + " returned " + response.statusCode() + ": " + abbreviate(body));
            }
            return MAPPER.readTree(body);
        }
        catch (PrestoException exception) {
            throw exception;
        }
        catch (Exception exception) {
            throw new PrestoException(HIVE_METASTORE_ERROR, "Unity Catalog GET " + url + " failed: " + exception.getMessage(), exception);
        }
    }

    private static String abbreviate(String body)
    {
        if (body.length() <= 2000) {
            return body;
        }
        return body.substring(0, 2000);
    }
}
