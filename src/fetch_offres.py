# /// script
# dependencies = [
#   "pandas",
#   "pyarrow",
#   "requests",
#   "markdownify",
#   "trafilatura",
# ]
# ///

import requests
import pandas as pd
import os
import re
import time
import trafilatura
from datetime import datetime, timedelta
from markdownify import markdownify as md

URL = "https://data.gouv.nc/api/explore/v2.1/catalog/datasets/offres-d-emploi-deposees-sur-le-site-emploi-nc/exports/parquet?lang=fr&timezone=Pacific%2FNoumea"
OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "offres.csv")
WORK_DIR = "work"

def clean_text_for_markdown(text):
    if not text:
        return ""
    
    # 1. Handle exotic bullet points often found in copy-pasted text (Microsoft Word, etc.)
    # These characters:  (F02D), •, , , etc.
    # We replace them with a standard dash if they are at the start of a line
    text = re.sub(r'(?m)^[ \t]*[•\*][ \t]*', '- ', text)
    
    return text

def fetch_web_content_as_md(uuid):
    """Fetch the full content of the offer from the website and convert to clean MD locally"""
    target_url = f"https://emploi.nc/offers/{uuid}"
    
    try:
        # We fetch the HTML directly
        downloaded = trafilatura.fetch_url(target_url)
        if downloaded:
            # Extract content and convert to MD
            result = trafilatura.extract(downloaded, include_links=True, include_images=False, output_format='markdown')
            if result:
                return result.strip()
    except Exception as e:
        print(f"Error fetching web content for {uuid}: {e}")
    
    return None

def main():
    print(f"Downloading data from {URL}...")
    try:
        df = pd.read_parquet(URL)
    except Exception as e:
        print(f"Error downloading or reading parquet: {e}")
        return
    
    print(f"Initial count: {len(df)}")
    
    # Identify status column
    status_col = 'statut' if 'statut' in df.columns else 'status'
    
    if status_col in df.columns:
        print(f"Filtering using column: {status_col}")
        df_filtered = df[df[status_col] != 'INACTIVE']
        print(f"Filtered count (status != 'INACTIVE'): {len(df_filtered)}")
    else:
        print("Warning: Could not find status column. Saving all records.")
        df_filtered = df

    # Sort by date to have most recent first if column exists
    date_col = 'created_at' if 'created_at' in df_filtered.columns else None
    if date_col:
        df_filtered = df_filtered.sort_values(by=date_col, ascending=False)

    # Save CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df_filtered.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
    print(f"Saved to {OUTPUT_FILE}")

    # Setup directories
    for d in [WORK_DIR, OUTPUT_DIR]:
        os.makedirs(d, exist_ok=True)

    print(f"Generating files in {WORK_DIR} and {OUTPUT_DIR}...", flush=True)
    
    # Track generated UUIDs for cleanup
    current_uuids = set()
    
    # Calculate threshold for full content enrichment (last 7 days)
    one_week_ago = datetime.now() - timedelta(days=7)
    # Note: we'll handle the timezone comparison carefully during the loop
    
    count = 0
    total = len(df_filtered)
    enrichment_count = 0
    
    for _, row in df_filtered.iterrows():
        uuid = str(row['uuid'])
        current_uuids.add(uuid)
        
        # Periodic progress logging
        if (count + 1) % 50 == 0 or (count + 1) == total:
            print(f"Progress: {count + 1}/{total} offers processed (Enriched: {enrichment_count})...", flush=True)
            
        description = str(row['description']) if pd.notna(row['description']) else ""
        
        # Save raw to work
        with open(os.path.join(WORK_DIR, f"{uuid}.txt"), "w", encoding="utf-8") as f:
            f.write(description)
        
        # Check if offer is recent enough for full content enrichment
        is_recent = False
        if 'created_at' in row and pd.notna(row['created_at']):
            created_at = pd.to_datetime(row['created_at'])
            # Normalize both to offset-aware or naive to compare
            if created_at.tzinfo is not None:
                # Compare as offset-aware (using UTC for current time if needed)
                if created_at >= (datetime.now(created_at.tzinfo) - timedelta(days=7)):
                    is_recent = True
            else:
                if created_at >= one_week_ago:
                    is_recent = True

        # Try to fetch full content from web locally for recent offers
        web_md = None
        if is_recent:
            enrichment_count += 1
            print(f"[{enrichment_count}] Fetching full content for recent offer {uuid}...", flush=True)
            web_md = fetch_web_content_as_md(uuid)
        
        if web_md:
            clean_md = web_md
        else:
            # Pre-clean the description to catch "fake" lists before HTML conversion
            pre_cleaned = clean_text_for_markdown(description)
            # Save cleaned markdown to docs/
            clean_md = md(pre_cleaned).strip()
            # Post-process: ensure list items have a newline before them if they don't
            clean_md = re.sub(r'([^\n])\n- ', r'\1\n\n- ', clean_md)
        
        titre = row['titre'] if 'titre' in row else "Offre d'emploi"
        
        # Build metadata block
        metadata = []
        metadata.append(f"# {titre}")
        metadata.append("")
        metadata.append(f"- **Url**: https://emploi.nc/offers/{uuid}")
        
        # Handle Provinces
        provinces = []
        if 'region_sud' in row and (row['region_sud'] is True or str(row['region_sud']).lower() == 'true'):
            provinces.append("Province Sud")
        if 'region_nord' in row and (row['region_nord'] is True or str(row['region_nord']).lower() == 'true'):
            provinces.append("Province Nord")
        if 'region_ile' in row and (row['region_ile'] is True or str(row['region_ile']).lower() == 'true'):
            provinces.append("Province des Îles")
        
        if provinces:
            metadata.append(f"- **📍 Province**: {', '.join(provinces)}")
        
        # Handle Durée
        duree_parts = []
        if 'nb_annees_contrat' in row and pd.notna(row['nb_annees_contrat']) and int(row['nb_annees_contrat']) > 0:
            annees = int(row['nb_annees_contrat'])
            duree_parts.append(f"{annees} an{'s' if annees > 1 else ''}")
        if 'nb_mois_contrat' in row and pd.notna(row['nb_mois_contrat']) and int(row['nb_mois_contrat']) > 0:
            duree_parts.append(f"{int(row['nb_mois_contrat'])} mois")
        if 'nb_jours_contrat' in row and pd.notna(row['nb_jours_contrat']) and int(row['nb_jours_contrat']) > 0:
            jours = int(row['nb_jours_contrat'])
            duree_parts.append(f"{jours} jour{'s' if jours > 1 else ''}")
        
        if duree_parts:
            metadata.append(f"- **⏳ Durée**: {', '.join(duree_parts)}")
        
        for col in df_filtered.columns:
            if col in ['description', 'titre', 'region_sud', 'region_nord', 'region_ile', 'nb_jours_contrat', 'nb_mois_contrat', 'nb_annees_contrat']:
                continue
            
            val = row[col]
            if pd.notna(val):
                # Clean value and handle booleans/durations nicely if they are simple
                display_name = col.replace('_', ' ').capitalize()
                
                # Add emojis to common fields
                emoji_map = {
                    'uuid': '🆔',
                    'ridet': '🏢',
                    'statut': '⚙️',
                    'type contrat': '📄',
                    'ville physique': '🏙️',
                    'diplome': '🎓',
                    'nb postes': '👥',
                }
                prefix = emoji_map.get(display_name.lower(), '-')
                
                # Format boolean values
                if isinstance(val, bool):
                    val = "Oui" if val else "Non"
                elif str(val).lower() == 'true':
                    val = "Oui"
                elif str(val).lower() == 'false':
                    val = "Non"
                
                # Format Ridet and Uuid with backticks
                if col.lower() in ['ridet', 'uuid']:
                    val = f"`{val}`"
                    
                # Always start with a dash to ensure it's a Markdown list item
                # and avoid lines being collapsed into a single paragraph
                metadata.append(f"- {prefix} **{display_name}**: {val}")

        md_content = "\n".join(metadata) + "\n\n---\n\n" + clean_md
        
        with open(os.path.join(OUTPUT_DIR, f"{uuid}.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        
        count += 1
        # No delay needed when using local conversion, but small one to be nice to emploi.nc
        if is_recent and enrichment_count % 10 == 0:
            time.sleep(0.5)
    
    # Generate index.md
    index_path = os.path.join(OUTPUT_DIR, "index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# 💼 Offres d'emploi en Nouvelle-Calédonie\n\n")
        f.write(f"Ce site regroupe les **{count}** offres d'emploi actives extraites de data.gouv.nc.\n\n")
        
        f.write("## 🚀 Dernières offres\n\n")
        
        # Add the first 20 offers with context
        for _, row in df_filtered.head(20).iterrows():
            uuid = str(row['uuid'])
            titre = row['titre'] if 'titre' in row else "Offre"
            type_c = row['type_contrat'] if 'type_contrat' in row else "?"
            ville = row['ville_physique'] if 'ville_physique' in row else "?"
            f.write(f"- [{titre}]({uuid}.md) ({type_c} - {ville})\n")
        
        f.write("\n\n---\n\n*Mis à jour automatiquement via GitHub Actions.*")

    # Cleanup orphaned files (files that are no longer in the CSV)
    print("Cleaning up orphaned files...")
    # Clean work directory (.txt)
    for f in os.listdir(WORK_DIR):
        if f.endswith(".txt"):
            uuid_part = f[:-4]
            if uuid_part not in current_uuids:
                os.remove(os.path.join(WORK_DIR, f))
    
    # Clean docs directory (.md)
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(".md") and f != "index.md":
            uuid_part = f[:-3]
            if uuid_part not in current_uuids:
                os.remove(os.path.join(OUTPUT_DIR, f))

    print(f"Processed {count} offers.")

if __name__ == "__main__":
    main()
