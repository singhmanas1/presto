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

import com.facebook.airlift.configuration.Config;
import com.facebook.airlift.configuration.ConfigDescription;
import jakarta.validation.constraints.NotNull;

public class UnityMetastoreConfig
{
    private String uri;
    private String catalog = "unity";

    @NotNull
    public String getUri()
    {
        return uri;
    }

    @Config("hive.metastore.unity.uri")
    @ConfigDescription("Base URL of the Unity Catalog server, for example http://127.0.0.1:8082")
    public UnityMetastoreConfig setUri(String uri)
    {
        this.uri = uri;
        return this;
    }

    @NotNull
    public String getCatalog()
    {
        return catalog;
    }

    @Config("hive.metastore.unity.catalog")
    @ConfigDescription("Unity Catalog name used to build catalog.schema.table")
    public UnityMetastoreConfig setCatalog(String catalog)
    {
        this.catalog = catalog;
        return this;
    }
}
