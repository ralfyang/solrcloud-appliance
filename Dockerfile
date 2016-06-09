FROM registry.opensource.zalan.do/stups/openjdk:8-26

RUN apt-get update && apt-get install -y wget python3 unzip

RUN wget -q -O - http://www.mirrorservice.org/sites/ftp.apache.org/lucene/solr/5.4.1/solr-5.4.1.tgz | tar -xzf - -C /opt \
    && mv /opt/solr-5.4.1 /opt/solr
RUN chmod -R 777 /opt/solr

RUN wget -q -O /opt/jolokia-jvm-1.3.2-agent.jar "http://search.maven.org/remotecontent?filepath=org/jolokia/jolokia-jvm/1.3.2/jolokia-jvm-1.3.2-agent.jar"

ADD solr.xml solr.xml
ADD zoo.cfg zoo.cfg
ADD log4j.properties /opt/solr/server/resources/log4j.properties

ADD configs /opt/solr/configs

ADD scripts /opt/solr/scripts
RUN chmod 777 /opt/solr/scripts/*

ADD startup.sh startup.sh
RUN chmod 777 startup.sh

ADD startup-local.sh startup-local.sh
RUN chmod 777 startup-local.sh

EXPOSE 8983 8778

WORKDIR /opt/solr/

CMD ["/bin/bash", "-c", "/startup.sh"]
