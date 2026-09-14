import json
import requests

class ComfyAdapter:

    def __init__(self, base_url: str, api_key: str, template_path: str):
        self.base_url = base_url
        self.gen_headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.simple_headers = {"X-API-Key": api_key}
        self.template_path = template_path


    def upload_input(self, img_data: bytes, name: str):
        '''
        supported formats are jpg and png
        name should be with file extensions
        '''
        name_parts = name.split(".")
        if not name_parts or name_parts[-1] not in ["jpg", "png"]:
            return None
        img_type = "image/" + name_parts[-1]
        url = f"{self.base_url}/api/upload/image"
        files = {'image': (name, img_data, img_type)}
        data = {'type': 'input', 'overwrite': True}
        response = requests.post(url, headers=self.simple_headers, files=files, data=data)
        return response.status_code == 200


    def request_generation(self, input_name: str, promt: str, random_seed: int) -> str:
        with open(self.template_path) as f:
            workflow = json.load(f)
        workflow["121"]["inputs"]["image"] = input_name
        workflow["75:74"]["inputs"]["text"] = promt
        workflow["75:73"]["inputs"]["noise_seed"] = random_seed

        url = f"{self.base_url}/api/prompt"
        print("url:", url)
        response = requests.post(url, headers=self.gen_headers, json={"prompt": workflow})
        print(response)
        result = response.json()
        prompt_id = result["prompt_id"]
        print(f"Job submitted: {prompt_id}")
        return prompt_id


    def get_status(self, promt_id: str) -> tuple:
        '''
        returns ("queue", None), ("processing", None), ("success","output file name"), ("error", None) or ("invalid", None)
        '''
        url = f"{self.base_url}/history/{promt_id}"
        response = requests.get(url, headers=self.simple_headers)
        result = response.json()
        if promt_id not in result:
            return ("processing", None)
        if result[promt_id]["status"]["status_str"] == "error":
            return ("error", None)
        if result[promt_id]["status"]["status_str"] == "success":
            return ("success", result[promt_id]["outputs"]["9"]["images"][0]["filename"])
        return ("invalid", None)


    def download_output(self, filename: str):
        url = f"{self.base_url}/api/view?filename={filename}&subfolder=&type=output"
        response = requests.get(url, headers=self.simple_headers)
        if response.status_code == 200:
            return response.content
        else:
            return None

