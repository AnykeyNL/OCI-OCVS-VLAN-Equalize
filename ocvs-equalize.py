import sys
import time
import oci

from ocimodules.functions import login, input_command_line, create_signer, check_oci_version, MyWriter

# Disable OCI CircuitBreaker feature
oci.circuit_breaker.NoCircuitBreakerStrategy()

#################################################
#           Application Configuration           #
#################################################
min_version_required = "2.88.0"
application_version = "25.06.27"

##########################################################################
# Main Program
##########################################################################

print ("OCI - OCVS Equilizer")
print ("This utiliy help you match the configuration of all ESXi hosts in a SDDC Cluster")
print ("=====================================================================================")
print ("")

check_oci_version(min_version_required)

# Check command line parameters
cmd = input_command_line()

# if logging to file, overwrite default print function to also write to file
if cmd.log_file != "":
    writer = MyWriter(sys.stdout, cmd.log_file)
    sys.stdout = writer

#################################################
# oci config and "login" check
######################################################
config, signer = create_signer(cmd.config_profile, cmd.is_instance_principals, cmd.is_delegation_token)
tenant_id = config['tenancy']

login(config, signer)

# Create an array of all the hosts in the OCI SDDC cluster specified in the cmd.cluster parameter as vmwarecluster OCID
# Initialize the SDDC client
sddc = oci.ocvp.SddcClient(config, signer=signer)
sddc_cluster = oci.ocvp.ClusterClient(config, signer=signer)
sddc_hosts = oci.ocvp.EsxiHostClient(config, signer=signer)
compute = oci.core.ComputeClient(config, signer=signer)
network = oci.core.VirtualNetworkClient(config, signer=signer)
search = oci.resource_search.ResourceSearchClient(config, signer=signer)

query = "query VmwareCluster resources"
sdetails = oci.resource_search.models.StructuredSearchDetails()
sdetails.query = query

print ("Getting all SDDC Clusters...")
try:
    result = oci.pagination.list_call_get_all_results(search.search_resources, sdetails).data
except oci.exceptions.ServiceError as response:
    print ("Error: {} - {}".format(response.code, response.message))
    result = oci.resource_search.models.ResourceSummaryCollection()
    result.items = []

if (len(result) == 0):
    print("No SDDC Clusters found..")
    exit()

clusters = []
try:
    for cluster in result:
        sddcinfo = sddc.get_sddc(cluster.identity_context['sddcId']).data
        pair = (sddcinfo.display_name, cluster.display_name, cluster.identifier, cluster.availability_domain if cluster.availability_domain is not None else "Multi-AD")
        clusters.append(pair)
except oci.exceptions.ServiceError as response:
    print ("Error: {} - {}".format(response.code, response.message))
    exit()

print ("\nSDDC Cluster:")
print ("===============")

for idx, cluster in enumerate(clusters):
    print("{}: {} - {} ".format(idx, cluster[0], cluster[1]), cluster[2])

selected_id = None
while selected_id is None:
    try:
        user_input = input("Please select the cluster by entering the corresponding ID: ").strip()
        selected_id = int(user_input)
        if selected_id < 0 or selected_id >= len(result):
            print("Invalid ID. Please enter a valid ID.")
            selected_id = None
    except ValueError:
        print("Invalid input. Please enter a numeric ID.")

clusterOCID = clusters[selected_id][2]

try:
    hosts = sddc_hosts.list_esxi_hosts(cluster_id=clusterOCID).data.items

except oci.exceptions.ServiceError as response:
    print("error {} - {}".format(response.code, response.message))
    exit()


# Get for all hosts the VLAN attachments and create an unique vnic_id-VLAN_id table
unique_pairs = set()

print ("\nCreating list of VLAN attachments of the current environment....")
for host in hosts:

    if host.lifecycle_state == "ACTIVE":
        attachments = compute.list_vnic_attachments(compartment_id=host.compartment_id, instance_id=host.compute_instance_id).data
        
        for attachment in attachments:
            if attachment.vlan_id and attachment.lifecycle_state == "ATTACHED":
                pair = (attachment.nic_index, attachment.vlan_id)
                unique_pairs.add(pair)

unique_vlan_array = list(unique_pairs)

vlans_toadd = set()
for host in hosts:
    if host.lifecycle_state == "ACTIVE":  
        attachments = compute.list_vnic_attachments(compartment_id=host.compartment_id, instance_id=host.compute_instance_id).data
        for pair in unique_vlan_array:
            if not any(attachment.lifecycle_state == "ATTACHED" and attachment.nic_index == pair[0] and attachment.vlan_id == pair[1] for attachment in attachments):
                vlan = network.get_vlan(vlan_id=pair[1]).data
                print(f"- Host {host.display_name} is missing NIC index {pair[0]} with VLAN ID {vlan.display_name}")
                pair = (host.compartment_id, host.compute_instance_id, host.display_name, pair[0], pair[1], vlan.display_name)
                vlans_toadd.add(pair)


if len(vlans_toadd) > 0:
    user_input = input("\nDo you want to attach the missing VLANs to the hosts? (yes/no): ").strip().lower()
    
    # Attaching the missing VLANs to the hosts
    if user_input == 'yes':
        for compartment_id, instance_id, hostname, nic_index, vlan_id, vlan_name in vlans_toadd:
            print(f"Attaching VLAN {vlan_name} on host {hostname} on NIC index {nic_index}.")
            vnicdetails = oci.core.models.CreateVnicDetails()
            vnicdetails.vlan_id = vlan_id
            vnicdetails.display_name = "{}-nic{}".format(vlan_name, nic_index)
            attachdetails = oci.core.models.AttachVnicDetails()
            attachdetails.create_vnic_details = vnicdetails
            attachdetails.display_name = "Attachment-{}-nic{}".format(vlan_name, nic_index)
            attachdetails.instance_id = instance_id
            attachdetails.nic_index = nic_index
            retry = True
            while retry:
                retry = False
                try:
                    response = compute.attach_vnic(attach_vnic_details=attachdetails)
                    time.sleep(2)
                except oci.exceptions.ServiceError as response:
                    if response.code == "Conflict":
                        time.sleep(2)
                        retry = True
                    else:
                        print("error {} - {}".format(response.code, response.message))
                except Exception as e:
                    print(f"Failed to attach VLAN {vlan_name} to host {instance_id}: {str(e)}")
    else:
        print("No changes made to VLAN attachments.")


else:
    print ("All hosts seem to have equal VLAN attachments!")








