from flask import Flask, render_template, request, jsonify
import subprocess

app = Flask(__name__)

services={"webui": None, "kohya": None}
service_cmd={"webui": ['/tools/stable-diffusion-webui/webui.sh', '-f', '--listen'],
             "kohya": ['/tools/kohya_ss/gui.sh', '--listen', '0.0.0.0', '--server_port', '7861', '--inbrowser']}

@app.route('/')
def index():
    return render_template('index.html')

def control_service(service, cmd):
    if service is None:
        print("starting service", cmd)
        service = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        print("killing service", cmd)
        service.kill()
        service = None
    return service

@app.route('/start_process', methods=['POST'])
def start_process():
    name = request.json.get('service_name', '')
    if name not in services:
      return jsonify({"status": False})
    services[name] = control_service(services[name], service_cmd[name])

    return jsonify({"status": services[name] is not None})

if __name__ == '__main__':
    app.run(debug=True)
