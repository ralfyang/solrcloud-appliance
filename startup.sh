#!/bin/bash
cp /solr.xml /data/
cp /zoo.cfg /data/
mkdir -p /data/logs

# Get currently active Zookeeper nodes
ZK_HOST=$(./scripts/get_zk_servers.py)
if [ "$?" -ne "0" ]; then
  echo "ERROR Could not get list of Zookeeper servers."
  echo "$ZK_HOST"
  exit 1
fi
export ZK_HOST

# Check version of configurations and update them if needed
./scripts/check_and_update_solr_configs.py

MEM_JAVA_PERCENT=20
MEM_TOTAL_KB=$(cat /proc/meminfo | grep MemTotal | awk '{print $2}')
MEM_JAVA_KB=$(($MEM_TOTAL_KB * $MEM_JAVA_PERCENT / 100))

JAVA_OPTS="$JAVA_OPTS -javaagent:/opt/jolokia-jvm-1.3.2-agent.jar=port=8778,host=0.0.0.0"
JAVA_OPTS="$JAVA_OPTS -Xloggc:/data/logs/solr_gc.log"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.port=48983"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.authenticate=false"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.ssl=false"
#JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.password.file=jmxremote.password"
#JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.access.file=jmxremote.access"

# Start solr cloud instance
/opt/solr/bin/solr start -cloud -f -s /data -m ${MEM_JAVA_KB}k -a "${JAVA_OPTS}"
