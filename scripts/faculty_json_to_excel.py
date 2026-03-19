import json
import pandas as pd
import os

def json_to_excel(input_path, output_path):
    print(f"Reading JSON from: {input_path}")
    
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    rows = []
    
    # Check if data is a list or a dict of IDs
    if isinstance(data, dict):
        for staff_id, info in data.items():
            row = {
                "Staff ID": staff_id,
                "Name / Faculty Name": info.get("name") or info.get("Faculty Name") or "Unknown",
                "Designation": info.get("designation", ""),
                "Department": info.get("department", ""),
                "Category": info.get("category", "Teaching Faculty"),
                "Email": info.get("email", ""),
                "Username": info.get("username", staff_id.lower()),
                "Password": info.get("password", staff_id + "123")
            }
            rows.append(row)
    elif isinstance(data, list):
        for info in data:
            staff_id = info.get("Staff ID") or info.get("ID") or "UNKNOWN"
            row = {
                "Staff ID": staff_id,
                "Name / Faculty Name": info.get("Name / Faculty Name") or info.get("Name") or info.get("Faculty Name") or "Unknown",
                "Designation": info.get("Designation", ""),
                "Department": info.get("Department", ""),
                "Category": info.get("Category", "Teaching Faculty"),
                "Email": info.get("Email", ""),
                "Username": info.get("Username", staff_id.lower()),
                "Password": info.get("Password", staff_id + "123")
            }
            rows.append(row)
            
    # Create DataFrame
    df = pd.DataFrame(rows)
    
    # Reorder columns if needed
    columns_ordered = [
        "Staff ID", "Name / Faculty Name", "Designation", 
        "Department", "Category", "Email", "Username", "Password"
    ]
    df = df[columns_ordered]
    
    # Save to Excel
    print(f"Saving {len(rows)} records to: {output_path}")
    df.to_excel(output_path, index=False)
    print("SUCCESS: Excel file created successfully!")

if __name__ == "__main__":
    json_file = r"C:\Users\Lenovo\Downloads\Faculty_data.json"
    excel_file = r"C:\Users\Lenovo\Downloads\Faculty_data_processed.xlsx"
    
    if os.path.exists(json_file):
        json_to_excel(json_file, excel_file)
    else:
        print(f"ERROR: Input file not found at {json_file}")
