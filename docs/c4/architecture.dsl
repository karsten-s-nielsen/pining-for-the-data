workspace "pining-for-the-data" "De-identified youth soccer tracking data pipeline and open dataset distribution" {

    model {
        dataOwner = person "Data Owner" "Records matches on Veo3, manages de-identification, publishes open data"
        researcher = person "Researcher / Consumer" "Uses open tracking data for soccer analytics research"

        veo3 = softwareSystem "Veo3 Camera" "Records youth club matches in broadcast mode" "External"
        metrica = softwareSystem "Metrica GameCloud" "Commercial CV tracking provider, produces 2D XY tracking from video" "External"
        respoVision = softwareSystem "Respo.Vision" "Commercial 3D pose tracking provider, 50+ keypoints per player (future)" "External"
        hfHub = softwareSystem "HuggingFace Hub" "Dataset hosting platform, serves Parquet via load_dataset()" "External"
        aws = softwareSystem "AWS" "S3 storage + API Gateway + Lambda for mock provider API (future)" "External"
        luxuryLakehouse = softwareSystem "luxury-lakehouse" "Serverless soccer analytics platform that ingests this data" "External"

        pfd = softwareSystem "pining-for-the-data" "CLI tooling to de-identify, convert, and publish youth soccer tracking data" {
            deidentify = container "De-identification Module" "Generates synthetic rosters, maps jersey numbers to fictional identities" "Python" "Module"
            formats = container "Format Handlers" "Reads provider-specific tracking data, applies de-identification, writes clean output" "Python, pandas" "Module"
            publish = container "Publisher" "Pushes de-identified Parquet to HuggingFace Hub with dataset card" "Python, huggingface_hub" "Module"
            mockApi = container "Mock API Handlers" "Lambda functions mimicking Metrica/Respo.Vision download protocol (future)" "Python, AWS Lambda" "Future"
            namePools = container "Name Pools" "324 male, 148 female, 201 last names, 352 cities from fictional universes" "JSON" "Database"
            rosters = container "Game Rosters" "Per-game de-identified roster JSON files with two-layer jersey mapping" "JSON" "Database"
        }

        # External flows
        veo3 -> metrica "Video uploaded for processing" "HTTPS"
        veo3 -> respoVision "Video uploaded for processing (future)" "HTTPS"
        metrica -> dataOwner "Returns 2D tracking CSV" "HTTPS"
        respoVision -> dataOwner "Returns 3D pose JSON (future)" "HTTPS"

        # Data owner flows
        dataOwner -> deidentify "Generates synthetic rosters" "CLI"
        dataOwner -> formats "Feeds raw provider CSV/JSON" "CLI"
        dataOwner -> publish "Triggers dataset publication" "CLI"

        # Internal flows
        deidentify -> namePools "Samples fictional names" "JSON read"
        deidentify -> rosters "Writes roster JSON" "JSON write"
        formats -> rosters "Reads roster for de-identification" "JSON read"
        formats -> deidentify "Uses TwoLayerMapping" "Python import"
        formats -> publish "Passes de-identified Parquet" "Local filesystem"
        publish -> hfHub "Pushes Parquet + dataset card" "HTTPS/API"

        # Distribution flows
        hfHub -> researcher "Serves tracking data" "load_dataset()"
        aws -> researcher "Serves tracking data via mock provider API (future)" "HTTPS/REST"
        mockApi -> aws "Deployed to" "Terraform"

        # Downstream
        researcher -> luxuryLakehouse "Ingests open data for analysis" "Python"
        hfHub -> luxuryLakehouse "Provides training/test data" "load_dataset()"
    }

    views {
        systemContext pfd "SystemContext" {
            include *
            autoLayout
        }

        container pfd "Containers" {
            include *
            autoLayout
        }

        dynamic pfd "IngestFlow" {
            dataOwner -> deidentify "Generates synthetic rosters"
            deidentify -> namePools "Samples fictional names"
            deidentify -> rosters "Writes roster JSON"
            dataOwner -> formats "Feeds raw provider CSV/JSON"
            formats -> rosters "Reads roster for de-identification"
            formats -> publish "Passes de-identified Parquet"
            publish -> hfHub "Pushes Parquet + dataset card"
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
            element "Module" {
                background #438DD5
                color #ffffff
            }
            element "Database" {
                shape Cylinder
            }
            element "Future" {
                background #666666
                color #ffffff
                border dashed
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
