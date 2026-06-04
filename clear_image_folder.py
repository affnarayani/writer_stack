import os
import shutil

def clear_folder(folder_path):
    # Check karna ki folder sach mein exist karta hai ya nahi
    if not os.path.exists(folder_path):
        print(f"Error: '{folder_path}' folder nahi mila.", flush=True)
        return

    # Folder ke andar ki har cheez par loop chalana
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            # Agar file ya symbolic link hai toh use delete karein
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            # Agar koi sub-folder hai toh use poora delete karein
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f"Failed to delete {file_path}. Reason: {e}", flush=True)
            
    print(f"'{folder_path}' folder ka content clear kar diya gaya hai!", flush=True)

# Folder ka path set karein
target_folder = "image"

# Function ko call karein
clear_folder(target_folder)