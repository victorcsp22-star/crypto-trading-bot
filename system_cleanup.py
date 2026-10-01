import os
import sys
import shutil
import subprocess
import json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def clean_temp_files():
    print("\n" + "="*70)
    print(" 🧹 LIMPIANDO ARCHIVOS TEMPORALES Y CACHÉ DE PYTHON / SISTEMA...")
    print("="*70)
    cleaned_bytes = 0
    cleaned_files = 0
    
    temp_dirs = [
        os.environ.get('TEMP'),
        os.environ.get('TMP'),
        os.path.expanduser('~\\AppData\\Local\\Temp')
    ]
    
    # 1. Limpieza de directorios TEMP
    for tdir in set(temp_dirs):
        if tdir and os.path.exists(tdir):
            for root, dirs, files in os.walk(tdir, topdown=False):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        cleaned_bytes += sz
                        cleaned_files += 1
                    except Exception:
                        pass
                for d in dirs:
                    dp = os.path.join(root, d)
                    try:
                        os.rmdir(dp)
                    except Exception:
                        pass

    # 2. Limpieza de Pip Cache
    try:
        res = subprocess.run([sys.executable, "-m", "pip", "cache", "purge"], capture_output=True, text=True)
        print(" • Pip Cache purge realizado con éxito.")
    except Exception:
        pass
        
    # 3. Limpieza de __pycache__ y .pyc en el área de trabajo
    scratch_dir = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, files in os.walk(scratch_dir):
        for d in dirs:
            if d == '__pycache__':
                dp = os.path.join(root, d)
                try:
                    shutil.rmtree(dp)
                    print(f" • Removido __pycache__: {dp}")
                except Exception:
                    pass
                    
    cleaned_mb = cleaned_bytes / (1024 * 1024)
    print(f" ✨ Limpieza de temporales completada: {cleaned_files} archivos eliminados ({cleaned_mb:.2f} MB liberados).\n")

def get_system_diagnostics():
    print("="*70)
    print(" 🖥️ DIAGNÓSTICO DE MEMORIA RAM Y PROCESOS ACTIVOS (WINDOWS)")
    print("="*70)
    
    # 1. Información de RAM y Disco C: vía PowerShell estandarizado
    ps_cmd = (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "$disk = Get-Volume C; "
        "[PSCustomObject]@{ "
        "TotalRAM_GB = [math]::Round($os.TotalVisibleMemorySize/1MB, 2); "
        "FreeRAM_GB = [math]::Round($os.FreePhysicalMemory/1MB, 2); "
        "TotalDisk_GB = [math]::Round($disk.Size/1GB, 2); "
        "FreeDisk_GB = [math]::Round($disk.SizeRemaining/1GB, 2) "
        "} | ConvertTo-Json"
    )
    
    try:
        res = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True)
        data = json.loads(res.stdout)
        
        total_ram = data["TotalRAM_GB"]
        free_ram = data["FreeRAM_GB"]
        used_ram = total_ram - free_ram
        ram_pct = (used_ram / total_ram) * 100
        
        print(f" • Memoria RAM Total      : {total_ram:.2f} GB")
        print(f" • Memoria RAM Usada      : {used_ram:.2f} GB ({ram_pct:.1f}%)")
        print(f" • Memoria RAM Libre      : {free_ram:.2f} GB")
        print(f" • Disco (C:) Libre       : {data['FreeDisk_GB']:.2f} GB / {data['TotalDisk_GB']:.2f} GB")
    except Exception as e:
        print(f" Error obteniendo métricas de SO: {e}")
        
    print("-" * 70)
    print(" 🔥 TOP 10 PROCESOS QUE MÁS MEMORIA RAM CONSUMEN:")
    print("-" * 70)
    
    ps_proc_cmd = (
        "Get-Process | Sort-Object WorkingSet64 -Descending | "
        "Select-Object -First 10 ProcessName, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB, 1)}} | "
        "ConvertTo-Json"
    )
    
    try:
        res_proc = subprocess.run(["powershell", "-Command", ps_proc_cmd], capture_output=True, text=True)
        procs = json.loads(res_proc.stdout)
        for p in procs:
            print(f" • {p['ProcessName']:<25} (PID: {p['Id']:<6}) : {p['RAM_MB']:>7.1f} MB RAM")
    except Exception as e:
        print(f" Error obteniendo procesos: {e}")
        
    print("="*70 + "\n")

if __name__ == "__main__":
    clean_temp_files()
    get_system_diagnostics()
