import csv
import sys

# Configure stdout to print UTF-8 correctly
sys.stdout.reconfigure(encoding='utf-8')

csv_path = r"d:\Projects\Dataset\updated_data.csv"

with open(csv_path, mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    print("Fieldnames:", reader.fieldnames)
    
    # Let's inspect the first 5 rows
    for i, row in enumerate(reader):
        if i >= 5:
            break
        print(f"\n--- Row {i+1} ---")
        for key, val in row.items():
            # Truncate long values for printing
            val_str = str(val)[:100] + ("..." if len(str(val)) > 100 else "")
            print(f"  {key}: {val_str}")
