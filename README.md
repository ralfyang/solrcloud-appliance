[![Build Status](https://travis-ci.org/zalando-incubator/solrcloud-appliance.svg?branch=master)](https://travis-ci.org/zalando-incubator/solrcloud-appliance?branch=master)
[![Coverage Status](https://codecov.io/github/zalando-incubator/solrcloud-appliance/coverage.svg?branch=master)](https://codecov.io/github/zalando-incubator/solrcloud-appliance?branch=master)

# SolrCloud appliance for STUPS

Appliance for running a SolrCloud on the [STUPS](https://stups.io/) infrastructure.

## Table of contents
1. SolrCloud appliance overview
2. Bootstrap SolrCloud appliance
3. Blue/green deployment of new SolrCloud appliance version

## 1 SolrCloud appliance overview

![SolrCloud setup](solrcloud-appliance.png)

## 2 Bootstrap SolrCloud appliance

1. Fork or clone this repository

        $ git clone https://github.com/zalando/solrcloud-appliance.git
        
2. Copy example.yaml and edit the new file

        $ cp example.yaml <application id>.yaml

    - **ApplicationId** - The ID of the SolrCloud cluster
    - **DockerImage** - The Docker image tag without version
    - **MintBucket** - The name of the S3 bucket for the secrets exchange via [mint](http://docs.stups.io/en/latest/components/mint.html)   
    - **ScalyrAccountKey** - The account key for [Scalyr](https://www.scalyr.com/) for storing the log output
    - **ZookeeperAPI** - URL to Zookeeper API for updating configurations, e.g. http://localhost:8181/exhibitor/v1
    - **Nodes** - The number EC2 instances which should be started
    - **SolrBaseUrl** - Base URL of SolrCloud for executing administration tasks, e.g. http://localhost:8983/solr 

3. Create the following security groups

    - \<application id\>
        - Inbound:

        | Type            | Protocol | Port Range | Source                          |
        | --------------- | -------- | ---------- | ------------------------------- |
        | All TCP         | TCP      | 0-65535    | sg-??? (\<application id\>)     |
        | SSH             | TCP      | 22         | sg-??? (Odd (SSH Bastion Host)) |
        | Custom TCP Rule | TCP      | 8983       | sg-??? (\<application id\>-lb   |
        | Custom TCP Rule | TCP      | 8778       | monitoring                      |

        - Outbound:

        | Type            | Protocol | Port Range | Source                          |
        | --------------- | -------- | ---------- | ------------------------------- |
        | All traffic     | All      | All        | 0.0.0.0/0                       |

    - \<application id\>-lb
        - Inbound:

        | Type            | Protocol | Port Range | Source                          |
        | --------------- | -------- | ---------- | ------------------------------- |
        | HTTPS           | TCP      | 443        | consumer                        |
        | HTTPS           | TCP      | 443        | sg-??? (\<application id\>      |

        - Outbound:        

        | Type            | Protocol | Port Range | Source                          |
        | --------------- | -------- | ---------- | ------------------------------- |
        | All traffic     | All      | All        | 0.0.0.0/0                       |

4. Create an IAM role named \<application id\> with the following policy ("AllowMintRead")

        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Resource": [
                        "arn:aws:s3:::<mint bucket>/<application id>/*"
                    ],
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject"
                    ],
                    "Sid": "AllowMintRead"
                }
            ]
        }

5. Deploy a Zookeeper (exhibitor) ensemble using the [exhibitor-appliance](https://github.com/zalando/exhibitor-appliance) for STUPS.

6. Build Solr

        $ docker build -t <tag> .

7. Smoke test Solr locally

        $ docker run -p 8983:8983 -p 8778:8778 --net=host -e "ZK_API=http://localhost:8181" -v /data -it <tag>

8. Push to Docker registry

        $ pierone login
        $ docker push <tag>

9. Deploy and bootstrap Solr cloud to AWS with [solrcloud-cli](https://github.com/zalando/solrcloud-cli).


## 3 Blue/green deployment of new SolrCloud appliance version
**Important:** Stop import of new data during deployment in order to not loose data in case one shards becomes
unavailable or something else happens during deployment.

1. Build new Solr version

        $ docker build -t <tag> .

2. Smoke test new Solr version locally

        $ docker run -p 8983:8983 -p 8778:8778 --net=host -e "ZK_API=http://localhost:8181" -v /data -it <tag>

3. Push to Docker registry

        $ pierone login
        $ docker push <tag>

4. Deploy new Solr version to inactive stack (green or blue) on AWS with [solrcloud-cli](https://github.com/zalando/solrcloud-cli).


## 4 Local standalone Solr

1. Start Docker container with /startup-local.sh command parameter

        $ docker run -p 8983:8983 -p 8778:8778 --net=host -v /data -it <tag> /startup-local.sh


## 5 Backup of SolrCloud cluster

### 1. Create S3 backup bucket \<backup bucket\>

### 2. Attach \<application id\>-backup policy to IAM role named \<application id\>

        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:*"
                    ],
                    "Resource": [
                        "arn:aws:s3:::<backup bucket>/"
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:*"
                    ],
                    "Resource": [
                        "arn:aws:s3:::<backup bucket>/*"
                    ]
                }
            ],
            "Version": "2012-10-17"
        }


### 3. Backup Solr collections
In order to enable automatic backups `SolrBackupBucket` and `BackupInterval` (weekly, daily, hourly) need to be set in 
the yaml configuration file.

### 4. Restore Solr collections
In order to bootstrap a new cluster from a backup the property `RestoreLatestBackup: True` needs to be set in the 
yaml configuration file. 
