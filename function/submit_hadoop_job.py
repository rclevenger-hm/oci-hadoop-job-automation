import oci
import paramiko
import json

def submit_hadoop_job(job_params):
    ssh_client = paramiko.SSHClient()
    ssh_client.load_system_host_keys()

    instance_ip = "YOUR_INSTANCE_IP"
    private_key_path = "/path/to/your/private/key"

    # Connect to the Hadoop cluster using SSH
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_key = paramiko.RSAKey(filename=private_key_path)
    ssh_client.connect(hostname=instance_ip, username="opc", pkey=ssh_key)

    # Submit the Hadoop job using SSH
    command = f'hadoop jar {job_params["jar_path"]} {job_params["job_class"]} {job_params["input_path"]} {job_params["output_path"]}'
    stdin, stdout, stderr = ssh_client.exec_command(command)

    # Close SSH connection
    ssh_client.close()

    return stdout.read()

def handle_request(request):
    try:
        job_params = {
            "jar_path": request.get("jar_path"),
            "job_class": request.get("job_class"),
            "input_path": request.get("input_path"),
            "output_path": request.get("output_path")
        }

        job_status = submit_hadoop_job(job_params)

        return {
            "message": "Hadoop job submitted successfully",
            "job_status": job_status.decode('utf-8')
        }
    except Exception as e:
        return {
            "error": str(e)
        }

def handler(ctx, data):
    try:
        request = json.loads(data.decode('utf-8'))
        response = handle_request(request)
        return response
    except Exception as e:
        return {
            "error": str(e)
        }
