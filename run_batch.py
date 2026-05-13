import csv
import json
import subprocess
import sys
import os
import time

PIPELINE_SCRIPT = "tekscrum_pipeline.py"
INPUT_CSV = "input_leads.csv"
OUTPUT_CSV = "output_emails.csv"

def main():
    if not os.path.exists(INPUT_CSV):
        print(f"\n❌ Error: '{INPUT_CSV}' file nahi mili!")
        print(f"Please ek '{INPUT_CSV}' file banayein aur usmein ye columns rakhein:")
        print("business_name, first_name, email, website, city, state, country, niche\n")
        
        # Create a sample input CSV automatically for the user
        with open(INPUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["business_name", "first_name", "email", "website", "city", "state", "country", "niche"])
            writer.writerow(["Direct Cremation OC", "Robert", "care@directcremationoc.com", "https://directcremationoc.com/", "Orange", "CA", "US", "direct cremation"])
        print(f"✅ Maine aapke liye ek sample '{INPUT_CSV}' file bana di hai. Usay Excel mein open karein aur apni leads daalein.\n")
        return

    # Read all leads from CSV
    leads = []
    with open(INPUT_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(row)

    if not leads:
        print(f"⚠️ '{INPUT_CSV}' khali hai. Pehle usmein leads add karein.")
        return

    print("=" * 60)
    print(f"🚀 TEKSCRUM BATCH RUNNER STARTING")
    print(f"📋 Total Leads Found: {len(leads)}")
    print("=" * 60)

    # Open output file in append mode (so if it crashes, you don't lose data)
    file_exists = os.path.exists(OUTPUT_CSV)
    
    with open(OUTPUT_CSV, 'a', newline='', encoding='utf-8') as out_file:
        writer = None
        
        for index, lead in enumerate(leads, 1):
            biz = lead.get('business_name', 'Unknown')
            print(f"\n⏳ Processing [{index}/{len(leads)}]: {biz}")
            
            lead['row_number'] = str(index)
            lead_json_str = json.dumps(lead)
            
            # Call the main pipeline
            start_time = time.time()
            result = subprocess.run(
                [sys.executable, PIPELINE_SCRIPT, lead_json_str],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            
            elapsed = round(time.time() - start_time, 1)
            
            try:
                # Find the JSON output from stdout
                output_lines = result.stdout.strip().split('\n')
                # The final JSON is always on the last line printed to stdout
                final_json = output_lines[-1] if output_lines else "{}"
                data = json.loads(final_json)
                
                status = data.get("status", "ERROR")
                qc = data.get("qc_status", "N/A")
                
                if status == "DONE":
                    print(f"   ✅ Success! (Time: {elapsed}s | QC: {qc})")
                else:
                    print(f"   ❌ Error: {data.get('error', 'Unknown Error')}")
                
                # Write header if this is the very first successful row
                if writer is None:
                    headers = list(lead.keys()) + list(data.keys())
                    if 'error' not in headers:
                        headers.append('error')
                    # remove duplicates preserving order
                    headers = list(dict.fromkeys(headers))
                    writer = csv.DictWriter(out_file, fieldnames=headers)
                    if not file_exists:
                        writer.writeheader()
                
                # Combine original lead data with API result
                combined_row = {**lead, **data}
                writer.writerow(combined_row)
                out_file.flush() # Save immediately
                
            except Exception as e:
                print(f"   ❌ Failed to parse output! (Time: {elapsed}s)")
                print(f"   Error Details: {str(e)}")

            if index < len(leads):
                print("   ⏳ Waiting 25 seconds before next lead to prevent API rate limits...")
                time.sleep(25)

    print("\n" + "=" * 60)
    print(f"🎉 BATCH COMPLETE!")
    print(f"📁 Check '{OUTPUT_CSV}' for your results.")
    print("=" * 60)

if __name__ == "__main__":
    main()
