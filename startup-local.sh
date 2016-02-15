#!/bin/bash
cp /solr.xml /data/
mkdir -p /data/logs

function core_properties() {
cat << _EOF_	
name=${1}
config=solrconfig.xml
schema=schema.xml
dataDir=data
_EOF_
}
 
CONFIGS_DIR=/opt/solr/configs
CORE_DIRS=/data
COLLECTIONS=$(find ${CONFIGS_DIR} -maxdepth 1 -mindepth 1 -type d -not -name '.*' -printf "%f\n")

for col in ${COLLECTIONS[*]}; do
   mkdir -p ${CORE_DIRS}/${col}
   cp -r ${CONFIGS_DIR}/${col} ${CORE_DIRS}/${col}/conf   
   core_properties ${col} > ${CORE_DIRS}/${col}/core.properties 
done

JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.port=48983"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.authenticate=false"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.ssl=false"

# Start solr standalone instance
echo
echo "You can access Solr via http://$(hostname -i):8983/solr" >&2
echo 
/opt/solr/bin/solr start -f -s /data -a "${JAVA_OPTS}"
