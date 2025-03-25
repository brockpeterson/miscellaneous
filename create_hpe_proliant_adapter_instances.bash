#!/bin/bash
# list of iLOs must be in /root/scripts/ilos.txt
# uses existing adapter instance credential as they are all the same, you'll want to change this

echo "What is the IP of your Operations primary node?"
read operations_primary_node
echo

echo "What is the local admin password?"
read admin_password
echo

curl -k -X POST "https://$operations_primary_node/suite-api/api/auth/token/acquire?_no_links=true" -H  "accept: application/json" -H  "Content-Type: application/json" -d "{  \"username\" : \"admin\",  \"authSource\" : \"local\",  \"password\" : \"$admin_password\"}" > /tmp/token.txt 2>/dev/null

echo "This is the Operations Token to be used in all subsequent API calls:" `cat /tmp/token.txt | awk -F'"' '{print $4}'` 
token=`cat /tmp/token.txt | awk -F'"' '{print $4}'`
echo
echo "Using this token: $token"
echo

for i in `cat /root/scripts/ilos.txt`
	do
	echo "Creating HPE Proliant Adapter Instances in VCF Operations..."
	echo "......................................................................"
	curl -k -X POST "https://$operations_primary_node/suite-api/api/adapters?extractIdentifierDefaults=false&force=true&_no_links=true" -H  "accept: application/json" -H  "Authorization: OpsToken $token" -H  "Content-Type: application/json" -d "{  \"name\": \"HPE Proliant Adapter Instance $i\",  \"description\": \"HPE Proliant Adapter Instance $i\",  \"collectorId\": \"1\",  \"adapterKindKey\": \"HPComputeAdapter\",  \"resourceKindKey\": \"hpcompute_adapter_instance\",  \"physicalDatacenterId\": \"\",  \"credential\": {    \"id\": \"c2bb102b-96d5-4e40-9e67-79c7c82e9f95\"  },  \"resourceIdentifiers\": [    {      \"name\": \"collect_events\",      \"value\": \"Yes\"    },    {      \"name\": \"discover_ports\",      \"value\": \"No\"    },    {      \"name\": \"host\",      \"value\": \"$i\"    },    {      \"name\": \"minimum_event_severity\",      \"value\": \"Info\"    },    {      \"name\": \"port\",      \"value\": \"443\"    },    {      \"name\": \"ssl_config\",      \"value\": \"No\"    },    {      \"name\": \"support_autodiscovery\",      \"value\": \"True\"    },    {      \"name\": \"threadpool_size\",      \"value\": \"10\"    },    {      \"name\": \"timeout\",      \"value\": \"300\"    },    {      \"name\": \"use_sha256_key_exchange\",      \"value\": \"Yes\"    }  ]}"
done
