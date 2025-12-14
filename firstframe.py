import cv2
import os

def extract_first_frames(video_folder, output_folder):
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)
    
    # Loop through all files in the folder
    for filename in os.listdir(video_folder):
        if filename.lower().endswith(".mp4"):
            video_path = os.path.join(video_folder, filename)
            cap = cv2.VideoCapture(video_path)
            
            # Read the first frame
            success, frame = cap.read()
            if success:
                # Construct output filename
                base_name = os.path.splitext(filename)[0]
                output_path = os.path.join(output_folder, f"{base_name}.jpg")
                
                # Save as JPG
                cv2.imwrite(output_path, frame)
                print(f"Saved first frame of {filename} -> {output_path}")
            else:
                print(f"Failed to read {filename}")
            
            cap.release()

# Example usage:
video_folder = r"C:\Users\qacer\Downloads\new\new"
output_folder = r"C:\Users\qacer\Downloads\new"
extract_first_frames(video_folder, output_folder)
