rm -r /tools/ComfyUI/models/diffusion_models /tools/ComfyUI/models/loras /tools/ComfyUI/models/vae /tools/ComfyUI/output
ln -s /models/stable-diffusion /tools/ComfyUI/models/diffusion_models
ln -s /models/stable-diffusion /tools/ComfyUI/models/checkpoints/
ln -s /models/flux/ /tools/ComfyUI/models/checkpoints/
ln -s /models/lora /tools/ComfyUI/models/loras
ln -s /models/vae /tools/ComfyUI/models/vae
ln -s /models/controlnet /tools/ComfyUI/models/controlnet
ln -s /models/text_encoders /tools/ComfyUI/models/text_encoders
ln -s /outputs/comfy /tools/ComfyUI/output
