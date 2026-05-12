# /// script
# dependencies = [
#   "pandas",
#   "pyarrow",
#   "requests",
#   "markdownify",
#   "trafilatura",
#   "feedgen",
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
from feedgen.feed import FeedGenerator

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

    # 2. Fix bold text spacing issues (e.g., "** Text **" -> "**Text**")
    # Python-Markdown (Zensical) can be picky about spaces inside bold markers
    text = re.sub(r'\*\*[ \t]+', '**', text)
    text = re.sub(r'[ \t]+\*\*', '**', text)

    # 3. Ensure space after bullets for better rendering in lists
    text = re.sub(r'(?m)^-[ \t]*([^\s])', r'- \1', text)

    # 4. Escape square brackets that are likely placeholders and not links
    # This avoids "unresolved link reference" warnings in Zensical
    # We look for [text] that is NOT followed by ( or : (link reference definition)
    text = re.sub(r'\[([^\]]+)\](?![(\:])', r'(\1)', text)

    # 5. Unescape markers that should be active (Trafilatura sometimes escapes them)
    text = text.replace(r'\*\*', '**')
    text = text.replace(r'\_', '_')
    
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

def generate_rss_feed(df_recent, output_path):
    """Generate an RSS feed for the most recent job offers"""
    fg = FeedGenerator()
    fg.id('https://adriens.github.io/emploi-nc/')
    fg.title("Offres d'emploi Nouvelle-Calédonie")
    fg.author({'name': 'Adrien', 'email': 'adrien@example.com'})
    fg.link(href='https://adriens.github.io/emploi-nc/', rel='alternate')
    fg.subtitle('Dernières offres d\'emploi extraites de data.gouv.nc')
    fg.language('fr')

    # Take the top 50 most recent offers
    for _, row in df_recent.head(50).iterrows():
        fe = fg.add_entry()
        uuid = str(row['uuid'])
        titre = row['titre'] if 'titre' in row else "Offre d'emploi"
        ville = row['ville_physique'] if 'ville_physique' in row else "N/A"
        type_c = row['type_contrat'] if 'type_contrat' in row else "N/A"
        
        fe.id(f'https://adriens.github.io/emploi-nc/{uuid}.md')
        fe.title(f"{titre} ({type_c} - {ville})")
        fe.link(href=f'https://adriens.github.io/emploi-nc/{uuid}.html')
        
        description = str(row['description']) if pd.notna(row['description']) else "Pas de description"
        fe.description(description[:500] + '...') # Short preview
        
        if 'created_at' in row and pd.notna(row['created_at']):
            # Ensure it's a datetime object
            dt = pd.to_datetime(row['created_at'])
            if dt.tzinfo is None:
                # Make it offset-aware if it's naive (assuming UTC or local)
                dt = dt.tz_localize('UTC')
            fe.published(dt)

    fg.rss_file(output_path)
    print(f"RSS feed generated at {output_path}")

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
            clean_md = clean_text_for_markdown(web_md)
        else:
            # Pre-clean the description to catch "fake" lists before HTML conversion
            pre_cleaned = clean_text_for_markdown(description)
            # Save cleaned markdown to docs/
            clean_md = md(pre_cleaned).strip()
            # Post-process: ensure list items have a newline before them if they don't
            clean_md = re.sub(r'([^\n])\n- ', r'\1\n\n- ', clean_md)
        
        titre = row['titre'] if 'titre' in row else "Offre d'emploi"
        entreprise = row['designation'] if 'designation' in row else None
        
        # Build metadata block
        metadata = []
        metadata.append(f"# {titre}")
        if entreprise:
            metadata.append(f"## {entreprise}")
        metadata.append("")

        # 1. Block: Essential Summary
        metadata.append("!!! info \"Synthèse de l'offre\"")
        metadata.append(f"    - :material-link-variant: **Lien direct** : [Voir l'annonce sur Emploi.nc](https://emploi.nc/offers/{uuid})")
        
        # Provinces
        provinces = []
        if 'region_sud' in row and (row['region_sud'] is True or str(row['region_sud']).lower() == 'true'):
            provinces.append("Province Sud")
        if 'region_nord' in row and (row['region_nord'] is True or str(row['region_nord']).lower() == 'true'):
            provinces.append("Province Nord")
        if 'region_ile' in row and (row['region_ile'] is True or str(row['region_ile']).lower() == 'true'):
            provinces.append("Province des Îles")
        if provinces:
            metadata.append(f"    - :material-map-marker-outline: **Localisation** : {', '.join(provinces)}")
        
        # Contract and Duration
        if 'type_contrat' in row and pd.notna(row['type_contrat']):
            metadata.append(f"    - :material-briefcase-outline: **Type de contrat** : {row['type_contrat']}")
            
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
            metadata.append(f"    - :material-calendar-clock: **Durée** : {', '.join(duree_parts)}")
        
        if 'ville_physique' in row and pd.notna(row['ville_physique']):
            metadata.append(f"    - :material-city-variant-outline: **Ville** : {row['ville_physique']}")
        
        if 'nb_postes' in row and pd.notna(row['nb_postes']):
            metadata.append(f"    - :material-account-group-outline: **Nombre de postes** : {row['nb_postes']}")
        metadata.append("")

        # 2. Block: Employer Details
        metadata.append("!!! abstract \"Informations Employeur\"")
        if 'ridet' in row and pd.notna(row['ridet']):
            ridet = str(row['ridet']).strip()
            metadata.append(f"    - **Ridet** : `{ridet}`")
            metadata.append(f"    - :material-office-building-marker: **Fiche Annuaire** : [Consulter sur gouv.nc](https://annuaire-entreprises.gouv.nc/recherche?q={ridet})")
        if 'enseigne' in row and pd.notna(row['enseigne']):
            metadata.append(f"    - **Enseigne** : {row['enseigne']}")
        if 'forme_juridique' in row and pd.notna(row['forme_juridique']):
            metadata.append(f"    - **Forme juridique** : {row['forme_juridique']}")
        if 'adresse_physique' in row and pd.notna(row['adresse_physique']):
            metadata.append(f"    - **Adresse** : {row['adresse_physique']}")
        metadata.append("")

        # 3. Block: Technical details (collapsible)
        metadata.append("??? quote \"Détails Techniques\"")
        metadata.append(f"    - **UUID** : `{uuid}`")
        for col in df_filtered.columns:
            # Skip fields already displayed
            if col in ['description', 'titre', 'region_sud', 'region_nord', 'region_ile', 
                      'nb_jours_contrat', 'nb_mois_contrat', 'nb_annees_contrat',
                      'type_contrat', 'ville_physique', 'nb_postes', 'ridet', 
                      'designation', 'enseigne', 'forme_juridique', 'adresse_physique', 'uuid']:
                continue
            
            val = row[col]
            if pd.notna(val):
                display_name = col.replace('_', ' ').capitalize()
                # Format boolean values
                if isinstance(val, bool):
                    val = "Oui" if val else "Non"
                elif str(val).lower() == 'true':
                    val = "Oui"
                elif str(val).lower() == 'false':
                    val = "Non"
                metadata.append(f"    - **{display_name}** : {val}")
        metadata.append("")

        md_content = "\n".join(metadata) + "\n---\n\n" + clean_md
        
        with open(os.path.join(OUTPUT_DIR, f"{uuid}.md"), "w", encoding="utf-8") as f:
            f.write(md_content)
        
        count += 1
        # No delay needed when using local conversion, but small one to be nice to emploi.nc
        if is_recent and enrichment_count % 10 == 0:
            time.sleep(0.5)
    
    # Generate index.md
    index_path = os.path.join(OUTPUT_DIR, "index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# :material-briefcase-search: Emplois en Nouvelle-Calédonie\n\n")
        f.write(f"Accédez aux **{count}** offres d'emploi actuellement actives sur le territoire.\n\n")
        
        f.write("## :material-clock-fast: Dernières publications\n\n")
        
        # Add the first 20 offers with context and icons
        for _, row in df_filtered.head(30).iterrows():
            uuid = str(row['uuid'])
            titre = row['titre'] if 'titre' in row else "Offre"
            type_c = row['type_contrat'] if 'type_contrat' in row else "?"
            ville = row['ville_physique'] if 'ville_physique' in row else "?"
            entreprise = row['designation'] if 'designation' in row else "Entreprise confidentielle"
            
            f.write(f"- **[{titre}]({uuid}.md)**  \n")
            f.write(f"    :material-domain: *{entreprise}* | :material-file-document-outline: {type_c} | :material-map-marker-outline: {ville}\n\n")
        
        f.write("\n\n---\n\n*Source des données : [data.gouv.nc](https://data.gouv.nc/) - Mis à jour quotidiennement.*")

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
    
    # Generate RSS Feed
    rss_path = os.path.join(OUTPUT_DIR, "feed.xml")
    generate_rss_feed(df_filtered, rss_path)

if __name__ == "__main__":
    main()
