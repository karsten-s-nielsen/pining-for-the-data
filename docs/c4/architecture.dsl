workspace "pining-for-the-data" "De-identified youth soccer tracking data pipeline and mock provider API" {

    model {
        researcher = person "Soccer Researcher" "Data scientist or analyst working with tracking data"
        dataOwner = person "Data Owner" "Records games, runs de-identification, uploads data"

        luxuryLakehouse = softwareSystem "luxury-lakehouse" "Serverless soccer analytics platform that ingests tracking data" "External"
        metrica = softwareSystem "Metrica Sports" "Commercial tracking data provider (GameCloud)" "External"
        hfHub = softwareSystem "HuggingFace Hub" "Dataset hosting platform (Level 1 distribution)" "External"
        veo = softwareSystem "Veo3 Camera" "Records youth soccer matches in broadcast mode" "External"

        pining = softwareSystem "pining-for-the-data" "De-identification pipeline and mock provider API for open soccer tracking data" {
            deidentify = container "De-identification Engine" "Generates synthetic rosters, maps jersey numbers to fictional identities" "Python" {
                namePools = component "Name Pools" "Loads and samples from fictional name lists (GOT, LOTR, BB, etc.)" "Python module"
                rosterGen = component "Roster Generator" "Produces per-game rosters with featured + random names" "Python module"
                mapping = component "Two-Layer Mapping" "Resolves jersey numbers to synthetic identities per game" "Python module"
            }
            formats = container "Format Handlers" "Reads provider-specific tracking data, applies de-identification, writes clean output" "Python" {
                metricaFmt = component "Metrica Format" "Reads/writes Metrica CSV (0-1 normalized XY)" "Python module"
                respoFmt = component "Respo.Vision Format" "Reads 3D pose data (scaffolded)" "Python module"
                convert = component "Format Converter" "Projects 3D to 2D (scaffolded)" "Python module"
            }
            publish = container "Publisher" "Pushes Parquet datasets to HuggingFace Hub with CC-BY-4.0 license" "Python"
            uploadCli = container "Upload CLI" "Uploads game artifacts to S3, maintains provider and match indexes" "Python (pining-upload)"
            apiGateway = container "API Gateway" "HTTP API routing requests to Lambda handlers" "AWS API Gateway v2 (HTTP)"
            listProviders = container "list_providers" "Returns list of supported tracking data providers" "AWS Lambda (Python 3.12)"
            listMatches = container "list_matches" "Returns available games and artifacts for a provider" "AWS Lambda (Python 3.12)"
            getArtifact = container "get_artifact" "Generates presigned S3 URL for artifact download" "AWS Lambda (Python 3.12)"
            dataBucket = container "Data Bucket" "Stores tracking data, metadata, and discovery indexes" "AWS S3 (KMS-encrypted)" "Database"
        }

        veo -> metrica "Broadcast footage uploaded to" "Video upload"
        metrica -> dataOwner "Delivers tracking CSV/XML via" "EPTS export"
        dataOwner -> deidentify "Runs de-identification pipeline" "CLI"
        dataOwner -> formats "Processes raw provider data" "CLI"
        dataOwner -> publish "Publishes datasets" "CLI (pining-publish)"
        dataOwner -> uploadCli "Uploads game artifacts" "CLI (pining-upload)"
        publish -> hfHub "Pushes Parquet files to" "HuggingFace Hub API"
        researcher -> hfHub "Downloads datasets via" "load_dataset()"
        researcher -> apiGateway "Queries mock provider API" "HTTPS + Bearer token"
        luxuryLakehouse -> apiGateway "Ingests tracking data from" "HTTPS + Bearer token"

        uploadCli -> dataBucket "Uploads artifacts and updates indexes" "AWS SDK (boto3)"
        apiGateway -> listProviders "Routes GET /providers" "AWS Lambda proxy"
        apiGateway -> listMatches "Routes GET /{provider}/matches" "AWS Lambda proxy"
        apiGateway -> getArtifact "Routes GET /{provider}/matches/{id}/{artifact}" "AWS Lambda proxy"
        listProviders -> dataBucket "Reads providers.json" "AWS SDK (boto3)"
        listMatches -> dataBucket "Reads {provider}/matches.json" "AWS SDK (boto3)"
        getArtifact -> dataBucket "Generates presigned URL for artifact" "AWS SDK (boto3)"
        formats -> deidentify "Uses roster mappings from" "Python import"

        production = deploymentEnvironment "AWS Production" {
            deploymentNode "AWS" "Amazon Web Services" "us-east-1" {
                deploymentNode "API Gateway" "HTTP API with throttling" "AWS API Gateway v2" {
                    containerInstance apiGateway
                }
                deploymentNode "Lambda" "Serverless compute" "Python 3.12, 128MB" {
                    containerInstance listProviders
                    containerInstance listMatches
                    containerInstance getArtifact
                }
                deploymentNode "S3" "Object storage" "KMS-encrypted, versioned" {
                    containerInstance dataBucket
                }
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

        component deidentify "DeidentifyComponents" {
            include *
            autoLayout
        }

        dynamic pining "ResearcherFlow" {
            researcher -> apiGateway "Requests match list with bearer token"
            apiGateway -> listMatches "Routes to list_matches Lambda"
            listMatches -> dataBucket "Reads metrica/matches.json"
            dataBucket -> listMatches "Returns match index JSON"
            listMatches -> apiGateway "Returns 200 with matches"
            apiGateway -> researcher "JSON response with game list"
            researcher -> apiGateway "Requests tracking artifact"
            apiGateway -> getArtifact "Routes to get_artifact Lambda"
            getArtifact -> dataBucket "Generates presigned URL"
            dataBucket -> getArtifact "Returns presigned URL"
            getArtifact -> apiGateway "Returns 302 redirect"
            apiGateway -> researcher "Redirects to presigned S3 URL"
            autoLayout
        }

        deployment pining "AWS Production" "Deployment" {
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
        }
    }

}
