workspace "pining-for-the-data" "Open soccer tracking data redistribution and mock provider API" {

    model {
        analyst = person "Soccer Analyst" "Researcher or data scientist analyzing tracking data"
        developer = person "Platform Developer" "Developer building ingestion pipelines against mock provider API"

        pining = softwareSystem "pining-for-the-data" "Redistributes MIT-licensed SkillCorner tracking data via HuggingFace Hub and a mock provider API" {
            ingestCli = container "Ingest CLI" "Validates SkillCorner V3 match JSON + tracking JSONL" "Python 3.12+, pining-ingest"
            rosterCli = container "Roster Generator CLI" "Generates synthetic rosters with fictional identities for future private data" "Python 3.12+, pining-generate-roster"
            deidentify = container "De-identification Engine" "Two-layer jersey-to-identity mapping using fictional name pools (GOT, LOTR, BB, etc.)" "Python 3.12+"
            formats = container "Format Handlers" "Read, write, and validate SkillCorner V3 format; Respo.Vision scaffolded" "Python 3.12+, JSON/JSONL"
            uploadCli = container "Upload CLI" "Uploads game artifacts to S3 and maintains discovery indexes" "Python 3.12+, boto3, pining-upload"
            publishCli = container "Publish CLI" "Pushes Parquet files and dataset cards to HuggingFace Hub" "Python 3.12+, huggingface_hub, pining-publish"
            apiGateway = container "API Gateway" "REST API with bearer token auth, stage-level throttling (10 rps)" "AWS API Gateway HTTP API"
            lambdaProviders = container "list_providers" "Returns JSON list of available tracking data providers" "AWS Lambda, Python 3.12"
            lambdaMatches = container "list_matches" "Returns JSON list of games and artifacts for a provider" "AWS Lambda, Python 3.12"
            lambdaArtifact = container "get_artifact" "Generates presigned S3 URL and returns 302 redirect" "AWS Lambda, Python 3.12"
            dataBucket = container "Data Bucket" "Stores tracking files organized by provider/game (match JSON, tracking JSONL, events CSV, phases CSV)" "AWS S3, KMS-SSE" "Database"
        }

        skillcorner = softwareSystem "SkillCorner Open Data" "MIT-licensed A-League tracking data (10 matches at 10fps)" "External"
        huggingface = softwareSystem "HuggingFace Hub" "Dataset hosting platform (Level 1 distribution)" "External"
        luxuryLakehouse = softwareSystem "luxury-lakehouse" "Serverless soccer analytics platform that ingests tracking data" "External"

        analyst -> huggingface "Downloads tracking data" "load_dataset() / HTTPS"
        developer -> apiGateway "Tests ingestion adapters against mock API" "HTTPS + Bearer token"
        analyst -> apiGateway "Downloads tracking artifacts" "HTTPS + Bearer token"
        luxuryLakehouse -> apiGateway "Ingests tracking data via provider adapter" "HTTPS + Bearer token"

        skillcorner -> formats "Source tracking data (git clone)" "Git LFS"

        ingestCli -> formats "Validates match + tracking files" "Python import"
        rosterCli -> deidentify "Generates synthetic rosters" "Python import"
        uploadCli -> dataBucket "Uploads game artifacts + indexes" "boto3 S3 API"
        publishCli -> huggingface "Pushes Parquet + dataset card" "HuggingFace API"

        apiGateway -> lambdaProviders "GET /v1/providers" "Lambda proxy"
        apiGateway -> lambdaMatches "GET /v1/{provider}/matches" "Lambda proxy"
        apiGateway -> lambdaArtifact "GET /v1/{provider}/matches/{id}/{artifact}" "Lambda proxy"
        lambdaProviders -> dataBucket "Reads providers.json" "S3 GetObject"
        lambdaMatches -> dataBucket "Reads {provider}/matches.json" "S3 GetObject"
        lambdaArtifact -> dataBucket "Lists + presigns artifact files" "S3 ListObjects + presign"

        production = deploymentEnvironment "Production" {
            deploymentNode "AWS" "Amazon Web Services" "us-east-1" {
                deploymentNode "API Gateway" "HTTP API with CORS, throttling (10 rps / 50 burst)" "AWS API Gateway v2" {
                    containerInstance apiGateway
                }
                deploymentNode "Lambda" "Serverless compute (5 reserved concurrency, X-Ray tracing)" "AWS Lambda, Python 3.12" {
                    containerInstance lambdaProviders
                    containerInstance lambdaMatches
                    containerInstance lambdaArtifact
                }
                deploymentNode "S3" "Object storage with KMS-CMK encryption and versioning" "AWS S3" {
                    containerInstance dataBucket
                }
            }
            deploymentNode "HuggingFace" "Dataset hosting platform" "SaaS" {
                hfInstance = softwareSystemInstance huggingface
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

        dynamic pining "ArtifactDownload" {
            analyst -> apiGateway "Requests match list with bearer token"
            apiGateway -> lambdaMatches "Routes to list_matches handler"
            lambdaMatches -> dataBucket "Reads skillcorner/matches.json"
            lambdaMatches -> apiGateway "Returns 200 with match index"
            apiGateway -> analyst "JSON response with game list"
            analyst -> apiGateway "Requests tracking artifact"
            apiGateway -> lambdaArtifact "Routes to get_artifact handler"
            lambdaArtifact -> dataBucket "Generates presigned URL for file"
            lambdaArtifact -> apiGateway "Returns 302 redirect"
            apiGateway -> analyst "Redirects to presigned S3 download"
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
