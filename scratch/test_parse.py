import csv
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

csv_path = r"d:\Projects\Dataset\updated_data.csv"

with open(csv_path, mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i == 5: # Row 6 (0-indexed 5)
            print("Row 6 eligibility:")
            print(row.get('eligibility'))
            print("\nRow 6 details:")
            print(row.get('details'))
            
            income_search = f"{row.get('eligibility')} {row.get('details')}"
            income_match = re.search(r'\b(?:income|limit)\b.*?\b(?:rs\.?|inr|₹)?\s*([\d,\.]+)\s*(?:lakh|l)?\b', income_search, re.IGNORECASE)
            if income_match:
                print("\nMatch:", income_match.group(0))
                print("Captured number:", income_match.group(1))
            break
