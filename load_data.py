import sys
import os
from database import load_csv_to_db, init_db

def main():
    # Make sure DB schema is created
    init_db()
    
    if len(sys.argv) < 2:
        print("Usage: python load_data.py <path_to_csv_file>")
        print("Example: python load_data.py data/schemes.csv")
        sys.exit(1)
        
    csv_path = sys.argv[1]
    
    if not os.path.exists(csv_path):
        print(f"Error: File '{csv_path}' does not exist.")
        sys.exit(1)
        
    print(f"Loading data from '{csv_path}'...")
    try:
        load_csv_to_db(csv_path)
    except Exception as e:
        print(f"An error occurred while loading the CSV data: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
