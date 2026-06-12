#!/usr/bin/env python3
"""
Notion Case Management Database Setup Script

Creates the required Notion database schema for the legal brief pipeline.
Run once before your first pipeline execution.

Usage:
    NOTION_TOKEN=your_token python3 scripts/notion-setup.py
"""

import os
import requests
import json

NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_VERSION = '2022-06-28'
HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json'
}

def create_case_database(parent_page_id: str) -> dict:
    """Create the legal brief tracking database in Notion."""
    
    database = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Legal Brief Tracker"}}],
        "properties": {
            "Name": {"title": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "Draft", "color": "yellow"},
                        {"name": "Built", "color": "green"},
                        {"name": "Filed", "color": "blue"},
                        {"name": "Error", "color": "red"}
                    ]
                }
            },
            "PDF URL": {"url": {}},
            "Last Built": {"date": {}},
            "Branch": {"rich_text": {}},
            "Word Count": {"number": {"format": "number"}},
            "Commit": {"url": {}},
            "Repository": {"rich_text": {}},
            "Court": {
                "select": {
                    "options": [
                        {"name": "Hawaii Family Court", "color": "blue"},
                        {"name": "Hawaii Circuit Court", "color": "purple"},
                        {"name": "9th Circuit", "color": "orange"},
                        {"name": "SCOTUS", "color": "red"},
                        {"name": "Federal District", "color": "gray"}
                    ]
                }
            },
            "Case Type": {
                "multi_select": {
                    "options": [
                        {"name": "Family Law", "color": "green"},
                        {"name": "Constitutional", "color": "red"},
                        {"name": "Employment", "color": "yellow"},
                        {"name": "Civil Rights", "color": "blue"},
                        {"name": "Motion", "color": "gray"}
                    ]
                }
            },
            "Due Date": {"date": {}}
        }
    }
    
    response = requests.post(
        'https://api.notion.com/v1/databases',
        headers=HEADERS,
        json=database
    )
    
    if response.status_code != 200:
        print(f'Error: {response.status_code}')
        print(response.json())
        return {}
    
    db = response.json()
    print(f'\n✅ Notion database created!')
    print(f'   Database ID: {db["id"]}')
    print(f'   URL: {db["url"]}')
    print(f'\nSet this secret in your GitHub repo:')
    print(f'   NOTION_DATABASE_ID={db["id"]}')
    return db

if __name__ == '__main__':
    if not NOTION_TOKEN:
        print('ERROR: Set NOTION_TOKEN env var')
        exit(1)
    
    parent = input('Enter the Notion page ID to create the database under: ').strip()
    if not parent:
        print('ERROR: Page ID required')
        exit(1)
    
    create_case_database(parent)
