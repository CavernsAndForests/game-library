"""
BGG Library Updater

Run this script whenever you add new games to your BGG collection:
    python update-library.py

The script will:
    1. Fetch your current collection from BoardGameGeek
    2. Fetch detailed info for each game (description, designers, etc.)
    3. Preserve any custom data you've added (complexity, location)
    4. Save everything to games.json
"""

import json
import os
import re
import html
import time
import urllib.request
import urllib.error
from datetime import datetime

# ============ CONFIGURATION ============
BGG_USERNAME = 'cavernsandforests'
BGG_API_TOKEN = '6c4f200d-ffa1-465c-8557-50098585e923'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, 'games.json')
# =======================================


def fetch_url(url, max_retries=10):
    """Fetch a URL with retry logic for BGG's processing queue."""
    for attempt in range(max_retries):
        print(f'  Fetching... (attempt {attempt + 1}/{max_retries})')
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'CavernsAndForestsLibraryUpdater/1.0',
                'Authorization': f'Bearer {BGG_API_TOKEN}',
            })
            with urllib.request.urlopen(req) as response:
                data = response.read().decode('utf-8')

                if 'will be processed' in data:
                    print('  BGG is processing request, waiting...')
                    time.sleep(3)
                    continue

                return data
        except urllib.error.HTTPError as e:
            if e.code == 202:
                print('  BGG is processing request, waiting...')
                time.sleep(3)
                continue
            raise Exception(f'HTTP {e.code}')
        except urllib.error.URLError as e:
            raise Exception(f'Network error: {e.reason}')

    raise Exception('Max retries exceeded - BGG took too long to respond')


def extract_links(xml_text, link_type):
    """Extract link values of a given type from BGG XML."""
    results = []
    pattern = re.compile(
        rf'link[^>]*type="{link_type}"[^>]*value="([^"]+)"', re.IGNORECASE
    )
    for match in pattern.finditer(xml_text):
        results.append(html.unescape(match.group(1)))
    return results


def parse_xml_value(xml_text, tag):
    """Extract text content from a simple XML tag."""
    match = re.search(rf'<{tag}[^>]*>([^<]*)</{tag}>', xml_text)
    return match.group(1).strip() if match else ''


def parse_xml_attr(xml_text, tag, attr):
    """Extract an attribute value from an XML tag."""
    match = re.search(rf'<{tag}[^>]*{attr}="([^"]*)"', xml_text, re.IGNORECASE)
    return match.group(1) if match else ''


def parse_collection(xml_text):
    """Parse BGG collection XML into a list of games."""
    games = []
    item_pattern = re.compile(
        r'<item[^>]*objectid="(\d+)"[^>]*>(.*?)</item>', re.DOTALL
    )

    for match in item_pattern.finditer(xml_text):
        game_id = match.group(1)
        item_xml = match.group(2)

        # Get BGG rating
        rating = 0.0
        avg_match = re.search(r'<average[^>]*value="([^"]+)"', item_xml)
        if avg_match:
            try:
                rating = float(avg_match.group(1))
            except ValueError:
                pass

        game = {
            'id': game_id,
            'name': parse_xml_value(item_xml, 'name'),
            'yearPublished': parse_xml_value(item_xml, 'yearpublished') or 'N/A',
            'image': parse_xml_value(item_xml, 'image'),
            'thumbnail': parse_xml_value(item_xml, 'thumbnail'),
            'minPlayers': int(parse_xml_attr(item_xml, 'stats', 'minplayers') or 1),
            'maxPlayers': int(parse_xml_attr(item_xml, 'stats', 'maxplayers') or 1),
            'playingTime': int(parse_xml_attr(item_xml, 'stats', 'playingtime') or 0),
            'rating': rating,
            # Fields populated by fetch_game_details
            'type': 'boardgame',
            'description': '',
            'shortDescription': '',
            'designers': [],
            'artists': [],
            'publishers': [],
            'categories': [],
            'mechanics': [],
            'honors': [],
            'minAge': 0,
            'bggWeight': 0.0,
            'bggRank': 0,
            'languageDependence': '',
            'baseGame': None,
            # Custom fields - preserved across updates
            'ourComplexity': '',
            'shelfLocation': '',
        }

        games.append(game)

    return games


def fetch_game_details(games):
    """Fetch detailed info for each game from the BGG Thing API."""
    batch_size = 20

    for i in range(0, len(games), batch_size):
        batch = games[i:i + batch_size]
        ids = ','.join(g['id'] for g in batch)
        end = min(i + batch_size, len(games))

        print(f'Fetching details for games {i + 1}-{end} of {len(games)}...')

        try:
            url = f'https://boardgamegeek.com/xmlapi2/thing?id={ids}&stats=1'
            xml_text = fetch_url(url)

            # Parse each item from the response
            item_pattern = re.compile(
                r'<item[^>]*id="(\d+)"[^>]*>(.*?)</item>', re.DOTALL
            )

            for match in item_pattern.finditer(xml_text):
                item_open_end = xml_text.index('>', match.start()) + 1
                item_open_tag = xml_text[match.start():item_open_end]
                game_id = match.group(1)
                item_xml = match.group(2)

                game = next((g for g in batch if g['id'] == game_id), None)
                if not game:
                    continue

                # Type (from item tag attribute)
                type_match = re.search(r'type="([^"]+)"', item_open_tag)
                game['type'] = type_match.group(1) if type_match else 'boardgame'

                # Description
                desc_match = re.search(r'<description>(.*?)</description>', item_xml, re.DOTALL)
                if desc_match:
                    # BGG double-encodes HTML entities in descriptions
                    game['description'] = html.unescape(html.unescape(desc_match.group(1)))
                    clean = re.sub(r'\s+', ' ', game['description']).strip()
                    if len(clean) > 200:
                        cutoff = clean.rfind(' ', 0, 200)
                        game['shortDescription'] = clean[:cutoff if cutoff > 0 else 200] + '...'
                    else:
                        game['shortDescription'] = clean

                # Designers, Artists, Publishers
                game['designers'] = extract_links(item_xml, 'boardgamedesigner')
                game['artists'] = extract_links(item_xml, 'boardgameartist')
                game['publishers'] = extract_links(item_xml, 'boardgamepublisher')

                # Categories & Mechanics
                game['categories'] = extract_links(item_xml, 'boardgamecategory')
                game['mechanics'] = extract_links(item_xml, 'boardgamemechanic')

                # Awards & Honors
                game['honors'] = extract_links(item_xml, 'boardgamehonor')

                # Min Age
                age_match = re.search(r'<minage[^>]*value="([^"]+)"', item_xml)
                game['minAge'] = int(age_match.group(1)) if age_match else 0

                # BGG Weight/Complexity (1-5 scale)
                weight_match = re.search(r'averageweight[^>]*value="([^"]+)"', item_xml)
                game['bggWeight'] = float(weight_match.group(1)) if weight_match else 0.0

                # BGG Rank
                rank_match = re.search(
                    r'rank[^>]*type="subtype"[^>]*name="boardgame"[^>]*value="([^"]+)"',
                    item_xml
                )
                if rank_match and rank_match.group(1) != 'Not Ranked':
                    try:
                        game['bggRank'] = int(rank_match.group(1))
                    except ValueError:
                        game['bggRank'] = 0
                else:
                    game['bggRank'] = 0

                # Language Dependence (winning vote from poll)
                lang_poll_match = re.search(
                    r'<poll[^>]*name="language_dependence"[^>]*>(.*?)</poll>',
                    item_xml, re.DOTALL
                )
                if lang_poll_match:
                    votes = []
                    result_pattern = re.compile(
                        r'<result[^>]*value="([^"]+)"[^>]*numvotes="(\d+)"'
                    )
                    for r_match in result_pattern.finditer(lang_poll_match.group(1)):
                        votes.append({
                            'value': r_match.group(1),
                            'count': int(r_match.group(2)),
                        })
                    if votes:
                        votes.sort(key=lambda v: v['count'], reverse=True)
                        game['languageDependence'] = votes[0]['value'] if votes[0]['count'] > 0 else ''

                # Base game (for expansions)
                if game['type'] == 'boardgameexpansion':
                    base_match = re.search(
                        r'<link[^>]*type="boardgameexpansion"[^>]*id="(\d+)"[^>]*value="([^"]+)"[^>]*inbound="true"',
                        item_xml
                    )
                    if base_match:
                        game['baseGame'] = {
                            'id': base_match.group(1),
                            'name': html.unescape(base_match.group(2)),
                        }

            # Small delay between batches
            if i + batch_size < len(games):
                time.sleep(1)

        except Exception as e:
            print(f'  Warning: Could not fetch details for batch: {e}')


def main():
    print('=' * 50)
    print('BGG Library Updater')
    print('=' * 50)
    print(f'\nFetching collection for: {BGG_USERNAME}\n')

    # Load existing data to preserve custom fields
    existing_data = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                for game in existing.get('games', []):
                    existing_data[game['id']] = {
                        'ourComplexity': game.get('ourComplexity', ''),
                        'shelfLocation': game.get('shelfLocation', ''),
                    }
            print(f'Found existing data for {len(existing_data)} games\n')
        except Exception:
            print('No existing data found, starting fresh\n')

    # Fetch collection from BGG
    print('Fetching collection from BGG...')
    collection_url = (
        f'https://boardgamegeek.com/xmlapi2/collection'
        f'?username={BGG_USERNAME}&own=1&stats=1'
    )
    collection_xml = fetch_url(collection_url)

    # Parse collection
    games = parse_collection(collection_xml)
    print(f'\nFound {len(games)} games in collection\n')

    if not games:
        print('No games found! Check that your BGG username is correct and your collection is public.')
        return

    # Fetch detailed info (descriptions, designers, categories, mechanics, etc.)
    print('Fetching game details (this may take a few minutes)...\n')
    fetch_game_details(games)

    # Merge with existing custom data
    for game in games:
        if game['id'] in existing_data:
            game['ourComplexity'] = existing_data[game['id']]['ourComplexity']
            game['shelfLocation'] = existing_data[game['id']]['shelfLocation']

    # Sort by name
    games.sort(key=lambda g: g['name'].lower())

    # Save to file
    output = {
        'lastUpdated': datetime.now().isoformat(),
        'username': BGG_USERNAME,
        'totalGames': len(games),
        'games': games,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Summary stats
    with_desc = sum(1 for g in games if g['description'])
    with_designers = sum(1 for g in games if g['designers'])
    with_honors = sum(1 for g in games if g['honors'])

    print('=' * 50)
    print(f'SUCCESS! Saved {len(games)} games to games.json')
    print(f'  - {with_desc} with descriptions')
    print(f'  - {with_designers} with designer info')
    print(f'  - {with_honors} with awards/honors')
    print('=' * 50)
    print('\nYour custom data (ourComplexity, shelfLocation) is preserved across updates.\n')


if __name__ == '__main__':
    main()
