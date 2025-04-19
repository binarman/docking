import subprocess
import psutil
from enum import Enum
from threading import Thread, Lock
import pyte
import os
import signal

def LOG(message):
  print("* CONTROL LOG: " + message)

class ServiceStatus(Enum):
    STOPPED = 1
    STARTING = 2
    RUNNING = 3
    ABORTING = 4

class StreamWatchdog(Thread):
    def __init__(self, data_stream, start_token: str, service: object):
        Thread.__init__(self)
        self.stream = data_stream
        self.token = start_token
        self.service = service

    def run(self):
        for line in self.stream:
            decoded = line.decode('utf-8')
            if len(decoded) > 1 and decoded[-1] == '\n':
              decoded = decoded[:-1]
            self.service.log_line(decoded)
            if self.token in decoded:
                self.service.notify_started()
        self.service.notify_stopped()

class Service:
    def __init__(self, name: str, cmd: list, running_message: str, start_marker: str):
        self.name = name
        self.cmd = cmd
        self.process = None
        self.status = ServiceStatus.STOPPED
        self.message = running_message
        self.start_marker = start_marker
        self.watchdog = None
        self.lock = Lock()
        self.max_history_len = 1000
        self.log = []
        self.last_log_id = 0

    def get_status(self):
        return self.status

    def get_message(self):
        if self.status == ServiceStatus.ABORTING:
            return "ABORTING"
        if self.status == ServiceStatus.RUNNING:
            return self.message
        elif self.status == ServiceStatus.STARTING:
            return "STARTING..."
        elif self.status == ServiceStatus.STOPPED:
            return "STOPPED"
        else:
            return "INTERNAL ERROR"

    def get_log(self, starting_from_id = 0):
        if starting_from_id == 0:
            return self.log
        else:
            return [[idx, message] for idx, message in self.log if idx >= starting_from_id]

    def notify_started(self):
        LOG("Service {} started".format(self.name))
        self.status = ServiceStatus.RUNNING

    def notify_stopped(self):
        with self.lock:
            self.process = None
            self.status = ServiceStatus.STOPPED
            LOG("Service {} finished".format(self.name))
            self.log_line("----==== FINISH ====----")

    def log_line(self, line):
        # do not want to trim anything from input, give enough space for processing
        tty_columns = len(line) + 1
        # emulate just one line, and add it to log after
        tty_rows = 1
        tty_screen = pyte.Screen(tty_columns, tty_rows)
        tty_stream = pyte.Stream(tty_screen)
        tty_stream.feed(line)

        self.last_log_id += 1
        processed_line = tty_screen.display[0].rstrip()
        self.log += [[self.last_log_id, processed_line]]
        self.log = self.log[-self.max_history_len:]

    def get_memory_consumption(self):
        with self.lock:
            if self.process is None:
                return {"ram": 0, "vram": 0}
            try:
                root_service_process = psutil.Process(self.process.pid)
                whole_tree = root_service_process.children(recursive=True) + [root_service_process]
                total_rss = 0
                service_pids = set()
                for p in whole_tree:
                    total_rss += p.memory_info().rss
                    service_pids.add(p.pid)
                total_vram = 0
                try:
                    import nvsmi
                    gpu_processes = nvsmi.get_gpu_processes()
                    for gpu_p in gpu_processes:
                        if gpu_p.pid in service_pids:
                            total_vram += gpu_p.used_memory * 1024 * 1024
                except:
                    LOG("Failed to gather NVidia GPU info")
                    pass
                return {"ram": total_rss, "vram": total_vram}
            except:
                return {"ram": 0, "vram": 0}

    def toggle(self):
        LOG("toggle process")
        if self.status == ServiceStatus.STOPPED:
            LOG("begin service start " + self.name)
            self.log_line("----==== START ====----")
            self.process = subprocess.Popen(self.cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.status = ServiceStatus.STARTING
            if self.watchdog is not None:
                self.watchdog.join()
            self.watchdog = StreamWatchdog(self.process.stdout, self.start_marker, self)
            self.watchdog.start()
            LOG("finish service start")
        else:
            with self.lock:
                LOG("killing service " + self.name)
                self.log_line("----==== ABORT ====----")
                self.status = ServiceStatus.ABORTING

                root_service_process = psutil.Process(self.process.pid)
                whole_tree = root_service_process.children(recursive=True) + [root_service_process]
                for p in whole_tree:
                    os.kill(p.pid, signal.SIGTERM)
                # the rest of finalization will be done by notify_stopped method called from watchdog

# Tests for service class

def test_service_timeout():
    from time import sleep
    s = Service("sleep", "sleep 1; echo hello; sleep 1", "RUNNING", "hello")
    assert s.get_status() == ServiceStatus.STOPPED
    assert s.get_message() == "STOPPED"
    s.toggle()
    assert s.get_status() == ServiceStatus.STARTING
    assert s.get_message() == "STARTING..."
    sleep(1.5)
    assert s.get_status() == ServiceStatus.RUNNING
    assert s.get_message() == "RUNNING"
    assert s.get_memory_consumption()["ram"] > 0
    sleep(1.5)
    assert s.get_status() == ServiceStatus.STOPPED
    assert s.get_message() == "STOPPED"
    assert s.get_log() == [[1, "----==== START ====----"], [2, "hello"], [3, "----==== FINISH ====----"]]

def test_service_abort():
    from time import sleep
    s = Service("sleep", "sleep 1", "RUNNING", "hello")
    assert s.get_status() == ServiceStatus.STOPPED
    assert s.get_message() == "STOPPED"
    s.toggle()
    assert s.get_status() == ServiceStatus.STARTING
    assert s.get_message() == "STARTING..."
    s.toggle()
    assert s.get_status() == ServiceStatus.ABORTING
    assert s.get_message() == "ABORTING"
    sleep(1)
    assert s.get_status() == ServiceStatus.STOPPED
    assert s.get_message() == "STOPPED"
    assert s.get_log() == [[1, "----==== START ====----"], [2, "----==== ABORT ====----"], [3, "----==== FINISH ====----"]]
    assert s.get_log(3) == [[3, "----==== FINISH ====----"]]
    assert s.get_memory_consumption() == {"ram": 0, "vram": 0}

# System mamangement

def get_system_status():
    resources = []
    ram = psutil.virtual_memory()
    resources += [["RAM", ram.total - ram.available, ram.total]]
    try:
        import nvsmi
        for gpu in nvsmi.get_gpus():
            gpu_name = "{} {} VRAM".format(gpu.name, gpu.id)
            gpu_total = gpu.mem_total * 1024 * 1024
            gpu_free = gpu.mem_free * 1024 * 1024
            resources += [[gpu_name, gpu_total - gpu_free, gpu_total]]
    except:
        LOG("Can not get nvidia gpu system info")
    return resources

# Web server part

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

services={}

@app.route('/')
def index():
    LOG("request: fetching page")
    return render_template('index.html')

@app.route('/service/toggle', methods=['POST'])
def toggle_service():
    name = request.json.get('service_name', '')
    LOG("request: service toggle: {}".format(name))
    if name not in services:
        return jsonify({"status": False})
    services[name].toggle()
    return jsonify({"status": True})

@app.route('/service/status', methods=['POST'])
def status():
    LOG("request: service status")
    last_log_id = request.json
    LOG("requested update starting from log ids: {}".format(last_log_id))
    statuses = {}
    for name in services:
        s = services[name]
        if not name in last_log_id:
            starting_log_id = 0
        else:
            starting_log_id = last_log_id[name] + 1
        statuses[name] = {"status": s.get_status().name, "message": s.get_message(), "log": s.get_log(starting_log_id), "memory": s.get_memory_consumption()}
    statuses["system"] = get_system_status()
    return jsonify(statuses)

if __name__ == '__main__':
    services["webui"] = Service("webui", '/tools/stable-diffusion-webui/webui.sh -f --listen --api', 'RUNNING goto http://127.0.0.1:7860', 'Startup time:')
    services["kohya"] = Service("kohya", '. /tools/python_3.10_venv/bin/activate; LD_LIBRARY_PATH=/tools/kohya_ss/venv/lib/python3.10/site-packages/nvidia/cuda_nvrtc/lib/:$LD_LIBRARY_PATH /tools/kohya_ss/gui.sh --listen 0.0.0.0 --server_port 7861 --inbrowser', 'RUNNING goto http://127.0.0.1:7861', 'Using shell=True when running external commands...')
    app.run(host="0.0.0.0", debug=True, port=5000)

