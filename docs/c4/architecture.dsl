workspace "pining-for-the-data" "Open + restricted soccer tracking data redistribution and mock provider API with two-tier auth" {

    model {
        analyst = person "Soccer Analyst" "Researcher or data scientist analysing tracking data"
        developer = person "Platform Developer" "Developer building ingestion adapters against the mock API"
        operator = person "Operator" "Repo owner; uploads data, manages tokens, runs orchestrator scripts"

        pining = softwareSystem "pining-for-the-data" "Redistributes MIT-licensed SkillCorner tracking data via HuggingFace Hub and serves both open and operator-loaded restricted content via a two-tier mock provider API" {
            ingestCli = container "Ingest CLI" "Validates SkillCorner V3 match JSON + tracking JSONL" "Python 3.12+, pining-ingest"
            rosterCli = container "Roster Generator CLI" "Generates synthetic rosters with fictional identities for de-identification" "Python 3.12+, pining-generate-roster"
            deidentify = container "De-identification Engine" "Two-layer jersey-to-identity mapping using fictional name pools" "Python 3.12+"
            formats = container "Format Handlers" "Read, write, and validate SkillCorner V3 format" "Python 3.12+, JSON/JSONL"
            uploadCli = container "Upload CLI (Matches)" "Uploads game artifacts; --visibility public|private; validates against MatchEntry Pydantic model" "Python 3.12+, boto3, pining-upload"
            uploadPlayersCli = container "Upload CLI (Players)" "Canonical-JSON-only player catalogue uploads; rejects CSV with reference to Gradient Sports orchestrator" "Python 3.12+, boto3, pining-upload-players"
            publishCli = container "Publish CLI" "Pushes Parquet files and dataset cards to HuggingFace Hub" "Python 3.12+, huggingface_hub, pining-publish"
            gradientOrchestrator = container "Gradient Sports Orchestrator" "One-shot script: reshape Gradient Sports source layout, normalise CSV to canonical JSON, drive uploadCli + uploadPlayersCli for the bulk load" "Python 3.12+, scripts/upload_gradient_wc2022.py"
            verifyScript = container "Verify Script" "Post-load HTTP verification: counts, visibility-leak checks, content-agnostic spot-check sampling" "Python 3.12+, scripts/verify_gradient_load.py"

            apiGateway = container "API Gateway" "REST API with bearer token auth, throttled (10 rps / 50 burst)" "AWS API Gateway HTTP API"
            lambdaProviders = container "list_providers Lambda" "Returns provider catalogue (tier-blind)" "AWS Lambda, Python 3.12"
            lambdaMatches = container "list_matches Lambda" "Returns match index for a provider; tier-filtered with optional query params (updatedSince, dateFrom, dateTo)" "AWS Lambda, Python 3.12"
            lambdaArtifact = container "get_artifact Lambda" "Looks up filename via artifacts dict; returns 302 + presigned URL" "AWS Lambda, Python 3.12" {
                validateToken = component "validate_token" "Returns Tier.PUBLIC / Tier.OWNER / 401; PUBLIC on duplicate (fail closed)" "Python function, hmac.compare_digest"
                ownerTokenFetcher = component "_get_owner_token" "Fetches owner token from SSM; functools.cache for warm-container lifetime" "Python, boto3 SSM"
                pathValidator = component "validate_path_param" "Rejects empty / oversized / `_`-prefixed path parameters" "Python, regex"
                artifactResolver = component "Artifact resolver" "Dict lookup in artifacts object; rejects names not in the allowlist" "Python, dict"
                presignBuilder = component "Presigned URL builder" "S3 generate_presigned_url with SigV4 signing" "boto3, KMS-aware"
            }
            lambdaPlayers = container "list_players Lambda" "Provider-gated 404 + tier-aware merge with private-wins precedence; supports updatedSince filtering" "AWS Lambda, Python 3.12"
            lambdaPlayer = container "get_player Lambda" "Single record lookup; provider-gated; private-wins precedence" "AWS Lambda, Python 3.12"
            lambdaHealth = container "health Lambda" "Unauthenticated health check; returns 200 OK for synthetic monitoring" "AWS Lambda, Python 3.12"

            dataBucket = container "Data Bucket (S3)" "Tracking files; public content at {provider}/...; private at {provider}/_private/...; SSE-KMS; versioned" "AWS S3" "Database"
            auditBucket = container "Audit Bucket (S3)" "CloudTrail data events; 365-day lifecycle; SSE-KMS; versioned" "AWS S3" "Database"
            cloudtrail = container "CloudTrail" "Data events on data bucket; excludes only providers.json reads" "AWS CloudTrail"
            kmsKey = container "KMS CMK" "Encrypts data bucket, audit bucket, and SSM SecureString" "AWS KMS"
            ssmParam = container "SSM Parameter Store" "Owner-tier bearer token (SecureString, KMS-encrypted)" "AWS SSM"
            canonicalModels = container "Canonical Models (Pydantic)" "MatchEntry + PlayerRecord; src/canonical/models.py — outside Lambda src so the runtime stays pydantic-free (ADR 0006)" "Python 3.12+, Pydantic v2"
            schemas = container "Published JSON Schemas" "matches.schema.json + players.schema.json with URN $id, drift-tested in CI" "schemas/, generated from canonical models"

            snsTopic = container "Alarm Topic (SNS)" "Delivers alarm notifications to operator email" "AWS SNS"
            cwDashboard = container "CloudWatch Dashboard" "Per-Lambda invocations, errors, duration (p99), throttles; API Gateway request count, 4xx, 5xx, latency" "AWS CloudWatch"
        }

        skillcorner = softwareSystem "SkillCorner Open Data" "MIT-licensed A-League tracking data" "External"
        huggingface = softwareSystem "HuggingFace Hub" "Dataset hosting platform (Level 1 distribution)" "External"
        luxuryLakehouse = softwareSystem "luxury-lakehouse" "Serverless soccer analytics platform; consumes the mock API with the owner token" "External"
        gradientSports = softwareSystem "Gradient Sports" "Source of restricted FIFA WC 2022 dataset; operator has download access" "External"

        analyst -> huggingface "Downloads tracking data" "load_dataset() / HTTPS"
        developer -> apiGateway "Tests ingestion adapters against the mock API" "HTTPS + public bearer token"
        analyst -> apiGateway "Downloads tracking artifacts" "HTTPS + public bearer token"
        luxuryLakehouse -> apiGateway "Ingests open + restricted tracking data" "HTTPS + owner bearer token"
        operator -> uploadCli "Uploads game artifacts" "Shell"
        operator -> uploadPlayersCli "Uploads player catalogue (canonical JSON)" "Shell"
        operator -> gradientOrchestrator "Bulk-loads Gradient Sports WC 2022" "Shell"
        operator -> verifyScript "Runs post-load verification" "Shell"
        operator -> ssmParam "Sets owner token (out-of-band)" "aws ssm put-parameter"

        skillcorner -> formats "Source tracking data (git clone)" "Git LFS"
        gradientSports -> gradientOrchestrator "Source bundle (operator-downloaded copy)" "Filesystem"

        ingestCli -> formats "Validates match + tracking files" "Python import"
        rosterCli -> deidentify "Generates synthetic rosters" "Python import"
        uploadCli -> dataBucket "Uploads game artifacts + matches.json index" "boto3 S3 API"
        uploadCli -> canonicalModels "Validates MatchEntry before any S3 write" "Python import"
        uploadPlayersCli -> dataBucket "Uploads {provider}/players.json or _private/players.json" "boto3 S3 API"
        uploadPlayersCli -> canonicalModels "Validates PlayerRecord before any S3 write" "Python import"
        publishCli -> huggingface "Pushes Parquet + dataset card" "HuggingFace API"
        gradientOrchestrator -> uploadCli "Drives per-match upload (visibility=private)" "subprocess / Python import"
        gradientOrchestrator -> uploadPlayersCli "Drives normalised player catalogue upload" "Python import"
        verifyScript -> apiGateway "Polls endpoints with both tokens; asserts post-conditions" "HTTPS"

        apiGateway -> lambdaProviders "GET /v1/providers" "Lambda proxy"
        apiGateway -> lambdaMatches "GET /v1/{provider}/matches" "Lambda proxy"
        apiGateway -> lambdaArtifact "GET /v1/{provider}/matches/{id}/{artifact}" "Lambda proxy"
        apiGateway -> lambdaPlayers "GET /v1/{provider}/players" "Lambda proxy"
        apiGateway -> lambdaPlayer "GET /v1/{provider}/players/{id}" "Lambda proxy"
        apiGateway -> lambdaHealth "GET /v1/health" "Lambda proxy"

        lambdaProviders -> dataBucket "Reads providers.json" "S3 GetObject"
        lambdaMatches -> dataBucket "Reads {provider}/matches.json; filters by tier + query params" "S3 GetObject"
        lambdaArtifact -> dataBucket "Reads matches.json then presigns artifact filename" "S3 GetObject + generate_presigned_url"
        lambdaPlayers -> dataBucket "Reads providers.json + players.json indexes (public + _private for OWNER)" "S3 GetObject"
        lambdaPlayer -> dataBucket "Same as list_players; finds by id with private-wins precedence" "S3 GetObject"

        lambdaProviders -> ssmParam "Fetches owner token (cached per warm container)" "boto3 SSM GetParameter"
        lambdaMatches -> ssmParam "Fetches owner token (cached per warm container)" "boto3 SSM GetParameter"
        lambdaArtifact -> ssmParam "Fetches owner token (cached per warm container)" "boto3 SSM GetParameter"
        lambdaPlayers -> ssmParam "Fetches owner token (cached per warm container)" "boto3 SSM GetParameter"
        lambdaPlayer -> ssmParam "Fetches owner token (cached per warm container)" "boto3 SSM GetParameter"

        ssmParam -> kmsKey "Decrypts SecureString" "KMS Decrypt"
        dataBucket -> kmsKey "Encrypts/decrypts objects" "SSE-KMS"
        auditBucket -> kmsKey "Encrypts log objects" "SSE-KMS"
        dataBucket -> cloudtrail "Emits data-event reads/writes" "CloudTrail data events"
        cloudtrail -> auditBucket "Writes log files" "S3 PutObject (gzipped JSON)"

        # Response paths (used by dynamic views)
        lambdaArtifact -> apiGateway "Returns 302 + presigned URL" "Lambda response"
        apiGateway -> luxuryLakehouse "302 redirect" "HTTPS"
        luxuryLakehouse -> dataBucket "Follows presigned URL (direct S3 GET)" "HTTPS"

        canonicalModels -> schemas "Generated by scripts/regenerate_schemas.py; drift-tested in CI" "Pydantic model_json_schema()"

        production = deploymentEnvironment "Production" {
            deploymentNode "AWS" "Amazon Web Services" "us-east-1" {
                deploymentNode "API Gateway" "HTTP API with CORS, throttling (10 rps / 50 burst)" "AWS API Gateway v2" {
                    containerInstance apiGateway
                }
                deploymentNode "Lambda" "Serverless compute (unreserved concurrency; X-Ray tracing; LAST_ROTATION env var for cache invalidation; security headers + structured JSON logging)" "AWS Lambda, Python 3.12" {
                    containerInstance lambdaProviders
                    containerInstance lambdaMatches
                    containerInstance lambdaArtifact
                    containerInstance lambdaPlayers
                    containerInstance lambdaPlayer
                    containerInstance lambdaHealth
                }
                deploymentNode "S3" "Object storage with KMS-CMK encryption and versioning" "AWS S3" {
                    containerInstance dataBucket
                    containerInstance auditBucket
                }
                deploymentNode "CloudTrail" "Trail with advanced event selectors (excludes only providers.json reads)" "AWS CloudTrail" {
                    containerInstance cloudtrail
                }
                deploymentNode "KMS" "Customer-managed key with annual rotation" "AWS KMS" {
                    containerInstance kmsKey
                }
                deploymentNode "SSM" "Parameter Store SecureString for owner token" "AWS SSM Parameter Store" {
                    containerInstance ssmParam
                }
                deploymentNode "Observability" "CloudWatch alarms, SNS notifications, dashboard (ADR 0007)" "AWS CloudWatch + SNS" {
                    containerInstance snsTopic
                    containerInstance cwDashboard
                }
            }
            deploymentNode "HuggingFace" "Dataset hosting platform" "SaaS" {
                hfInstance = softwareSystemInstance huggingface
            }
            deploymentNode "Operator Workstation" "Local Python environment with AWS credentials (devops-agent profile)" "macOS / Windows / Linux" {
                containerInstance ingestCli
                containerInstance rosterCli
                containerInstance uploadCli
                containerInstance uploadPlayersCli
                containerInstance publishCli
                containerInstance gradientOrchestrator
                containerInstance verifyScript
                containerInstance canonicalModels
                containerInstance schemas
            }
        }
    }

    views {
        systemContext pining "SystemContext" {
            include *
            autoLayout
        }

        container pining "Containers" {
            include *
            autoLayout
        }

        component lambdaArtifact "GetArtifactComponents" {
            include *
            autoLayout
        }

        dynamic pining "PrivateArtifactDownload" {
            luxuryLakehouse -> apiGateway "Requests private artifact with owner bearer token"
            apiGateway -> lambdaArtifact "Routes to get_artifact handler"
            lambdaArtifact -> ssmParam "Fetches owner token (warm-container cache miss only)"
            ssmParam -> kmsKey "Decrypts SecureString"
            lambdaArtifact -> dataBucket "Reads {provider}/matches.json"
            lambdaArtifact -> dataBucket "generate_presigned_url for {provider}/_private/{id}/{filename}"
            lambdaArtifact -> apiGateway "Returns 302 redirect"
            apiGateway -> luxuryLakehouse "302 with presigned S3 URL"
            luxuryLakehouse -> dataBucket "Follows presigned URL (direct S3 GET)"
            dataBucket -> cloudtrail "Records data-event read"
            cloudtrail -> auditBucket "Delivers log file"
            autoLayout
        }

        deployment pining production "Deployment" {
            include *
            autoLayout
        }

        styles {
            element "Person" {
                shape Person
                background #08427B
                color #ffffff
            }
            element "Software System" {
                background #1168BD
                color #ffffff
            }
            element "External" {
                background #999999
                color #ffffff
            }
            element "Container" {
                background #438DD5
                color #ffffff
            }
            element "Database" {
                shape Cylinder
            }
            element "Component" {
                background #85BBF0
                color #000000
            }
            relationship "Relationship" {
                color #707070
            }
        }
    }

}
