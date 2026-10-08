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

import java.net.URI;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Temporary cloud keys fetched from Unity Catalog, keyed by bucket.
 * The S3 client reads them when it opens that bucket.
 */
public final class UnityTableCredentials
{
    private static final ConcurrentHashMap<String, Credential> BY_BUCKET = new ConcurrentHashMap<>();

    private UnityTableCredentials() {}

    public static void put(String location, String accessKey, String secretKey, String sessionToken)
    {
        BY_BUCKET.put(bucket(location), new Credential(accessKey, secretKey, sessionToken));
    }

    public static Optional<Credential> lookup(URI uri)
    {
        if (uri == null) {
            return Optional.empty();
        }
        String bucket = uri.getHost();
        if (bucket == null || bucket.isEmpty()) {
            String authority = uri.getAuthority();
            if (authority == null || authority.isEmpty()) {
                return Optional.empty();
            }
            int at = authority.lastIndexOf('@');
            bucket = at >= 0 ? authority.substring(at + 1) : authority;
        }
        return Optional.ofNullable(BY_BUCKET.get(bucket));
    }

    public static String bucket(String location)
    {
        URI uri = URI.create(location);
        String bucket = uri.getHost();
        if (bucket == null || bucket.isEmpty()) {
            throw new IllegalArgumentException("Unity Catalog storage location has no bucket: " + location);
        }
        return bucket;
    }

    public static final class Credential
    {
        private final String accessKey;
        private final String secretKey;
        private final String sessionToken;

        public Credential(String accessKey, String secretKey, String sessionToken)
        {
            this.accessKey = accessKey;
            this.secretKey = secretKey;
            this.sessionToken = sessionToken == null || sessionToken.isEmpty() ? null : sessionToken;
        }

        public String getAccessKey()
        {
            return accessKey;
        }

        public String getSecretKey()
        {
            return secretKey;
        }

        public String getSessionToken()
        {
            return sessionToken;
        }
    }
}
