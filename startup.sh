#!/bin/bash -x
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

MEM_JAVA_PERCENT=30
MEM_TOTAL_KB=$(cat /proc/meminfo | grep MemTotal | awk '{print $2}')
MEM_JAVA_KB=$(($MEM_TOTAL_KB * $MEM_JAVA_PERCENT / 100))

JAVA_OPTS="$JAVA_OPTS -javaagent:/opt/jolokia-jvm-1.3.2-agent.jar=port=8778,host=0.0.0.0"
JAVA_OPTS="$JAVA_OPTS -Xloggc:/data/logs/solr_gc.log"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.port=48983"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.authenticate=false"
JAVA_OPTS="$JAVA_OPTS -Dcom.sun.management.jmxremote.ssl=false"

SOLR_BACKUP_DIR=/backup
mkdir -p $SOLR_BACKUP_DIR

# Start backup job as background process
if [ -n "$BACKUP_INTERVAL" ] && [[ "${BACKUP_INTERVAL}" =~ ^(weekly|daily|hourly|test)$ ]]
then
    [[ -z "${SOLR_BACKUP_BUCKET}" ]] && { echo "Parameter SOLR_BACKUP_BUCKET is empty" ; exit 1; }
    echo "Start backup job as background process"
    nohup ./scripts/solrcloud_backup.py -b "${SOLR_BACKUP_BUCKET}" -c "${BACKUP_INTERVAL}" backup &
else
    echo "Backup job is not configured to be started"
fi

# Restore latest available backup
if [ -n "$RESTORE_LATEST_BACKUP" ] && [[ "${RESTORE_LATEST_BACKUP}" =~ ^[tT][rR][uU][eE]$ ]]
then
    [[ -z "${SOLR_BACKUP_BUCKET}" ]] && { echo "Parameter SOLR_BACKUP_BUCKET is empty" ; exit 1; }
    export LATEST=$(aws s3 ls s3://"${SOLR_BACKUP_BUCKET}"/ | grep -E "[0-9]+/$" | sort -z | sed 's/.*PRE \([0-9]\+\)\//\1/' | tail -2)
    echo "Start to restore latest backup [${LATEST}]"
    nohup ./scripts/solrcloud_backup.py -b "${SOLR_BACKUP_BUCKET}" -t "${LATEST}" restore &
else
    echo "Startup with empty index, no backup will be restored"
fi

# Start solr cloud instance
/opt/solr/bin/solr start -cloud -f -s /data -m ${MEM_JAVA_KB}k -a "${JAVA_OPTS}"
