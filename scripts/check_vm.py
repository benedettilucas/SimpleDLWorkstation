import torch
import psutil
import subprocess

cuda_available = torch.cuda.is_available()
   
print("--- GPU info ---")
print(f"CUDA disponível no Colab: {cuda_available}")
if cuda_available:
    device_id = 0
    gpu_name = torch.cuda.get_device_name(device_id)
    print(f"Nome da GPU: {gpu_name}")
    print(f"Number of GPUs: {torch.cuda.device_count()}")    
    vram_total = torch.cuda.get_device_properties(device_id).total_memory / (1024**3)
    vram_alocada = torch.cuda.memory_allocated(device_id) / (1024**3)
    vram_livre = vram_total - vram_alocada
    print(f"VRAM Total da GPU: {vram_total:.2f} GB")
    print(f"VRAM Alocada pelo PyTorch: {vram_alocada:.2f} GB")
    print(f"VRAM Livre: {vram_livre:.2f} GB")
else:
    print("Nenhuma GPU ativa.")
print("--------------------------------\n")

# ==========================================
# 2. SISTEMA (CPU E RAM NO COLAB)
# ==========================================
print("--- System info ---")
resultado = subprocess.check_output("lscpu | grep 'Model name'", shell=True, text=True)
cpu = resultado.split(':')[1].strip()
print(f"Processador (CPU): {cpu}")
print(f"Nucleos Logicos (Threads): {psutil.cpu_count(logical=True)}")
ram_info = psutil.virtual_memory()
ram_total = ram_info.total / (1024**3)
ram_disponivel = ram_info.available / (1024**3)
print(f"RAM Total do Sistema: {ram_total:.2f} GB")
print(f"RAM Disponivel no Colab: {ram_disponivel:.2f} GB")
print(f"PyTorch version: {torch.__version__}")
print("--------------------------------\n")