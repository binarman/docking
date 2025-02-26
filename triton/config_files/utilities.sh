HAS_AMD_GPU=$(test -a /dev/kfd && test -a /dev/dri && echo 1)
HAS_NVIDIA_GPU=$(test -a /dev/nvidia0 && echo 1)

function get_device_suffix {

  if [[ $HAS_AMD_GPU = 1 ]]; then
    IMG_SUFFIX="amd"
  elif [[ $HAS_NVIDIA_GPU = 1 ]]; then
    IMG_SUFFIX="nvidia"
  else
    IMG_SUFFIX="nogpu"
  fi
  echo "$IMG_SUFFIX"
}
