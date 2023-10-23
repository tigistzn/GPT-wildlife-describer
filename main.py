import asyncio
import re
import os
import openai
from dotenv import load_dotenv
import aiohttp
import json

load_dotenv()

# Environment variables
api_key = os.getenv("OPENAI_API_KEY")

# the maximum word count for the returned animal descriptions
MAX_RETURN_WORD_COUNT = 100

def read_species_from_file(filename):
    """Read species from a JSON file."""
    with open(filename, 'r') as file:
        return json.load(file)

def write_species_to_file(filename, species_list):
    """Write species back to the JSON file after adding descriptions."""
    with open(filename, 'w') as file:
        json.dump(species_list, file, indent=4)

async def get_descriptive_text_from_wiki_async(animal_searchname, max_words=500, min_words = 100, language='en'):
    async with aiohttp.ClientSession() as session:
        # Search for the query
        search_url = f'https://{language}.wikipedia.org/w/api.php'
        search_params = {
            'action': 'query',
            'list': 'search',
            'format': 'json',
            'srsearch': animal_searchname
        }

        search_results = []

        async def connect_to_wiki(counter):
            try:
                async with session.get(search_url, params=search_params) as search_response:
                    search_results = await search_response.json()
                    search_results = search_results['query']['search']
                    return search_results
            except Exception as e:
                counter += 1
                print(f"Retrying to connect to wiki, error: {e}")
                if counter > 5:
                    return
                await asyncio.sleep(2)
                connect_to_wiki(counter)

        search_results = await connect_to_wiki(0)

        # Check if there are any results
        if not search_results:
            print(f"No results found for '{animal_searchname}' in '{language}' Wikipedia.")
            return None, None

        # Fetch the most likely page
        most_likely_page_title = search_results[0]['title']

        # Get the page content
        content_url = f'https://{language}.wikipedia.org/w/api.php'
        content_params = {
            'action': 'query',
            'prop': 'extracts',
            'format': 'json',
            'exsectionformat': 'wiki',
            'explaintext': 1,
            'titles': most_likely_page_title
        }
        async with session.get(content_url, params=content_params) as content_response:
            content_results = await content_response.json()
            page_id = list(content_results['query']['pages'].keys())[0]
            full_text = content_results['query']['pages'][page_id]['extract']

        # Extract summary and description sections
        # get a list of the different secitons
        sections = full_text.split('\n\n\n')
        section_dict = {}
        # split each section in a title and a body and add to the dict
        pattern = '==\s*(.*?)\s*=='
        section_dict['Summary'] = sections[0]
        exclude_titles = ['== External links ==', '== References ==']
        
        for section in sections[1:]: 
            match = re.search(pattern, section)
            if match:
                key = match.group(0)
                if key not in exclude_titles: 
                    content = section.split(key,1)
                    section_dict[key] = ''.join(content)
        
        
        description = ""
        # Combine summary and description
        for key, item in section_dict.items():
            if "description" in key.lower():
                description = item
                break
        
        gpt_input_text = section_dict['Summary'] + " " + description
        words = gpt_input_text.split()
        word_count = len(words)

        # if the summary + description are less than min_words, 
        # get all the other text and take max_words

        if word_count < min_words:
            gpt_input_text = " ".join(section_dict.values())
            words = gpt_input_text.split()
            word_count = len(words)
            result = " ".join(words[: min(word_count, max_words)])
            word_count = min(word_count, max_words)

        # Description and summary are good length
        elif word_count < max_words:
            result = " ".join(words)

        # Description and summary are too long
        else:
            result = " ".join(words[:max_words])
            word_count = max_words

        return result, word_count


async def coroutine_for_getting_and_writing_description(species: dict):
    """Get the description for a species and write it to the database."""

    print(f"Processing {species['genus']} {species['species']} ({species['common_name']})...")

    # Determine input prompt
    latin_name = (species['genus'] or "") + " " + (species['species'] or "")

    common_name = species['common_name']
    if not bool(common_name):
        common_name = latin_name

    species_id = species['species_id']
    wiki_text, in_word_count = await get_descriptive_text_from_wiki_async(latin_name)
    
    description = None

    if wiki_text: 
        # Set up the OpenAI API client
        openai.api_key = api_key
        return_word_count = min(MAX_RETURN_WORD_COUNT, in_word_count)
        params = {
            'model': 'gpt-3.5-turbo',
            'messages' : [
                {"role": "system", "content": f"You are an {common_name} talking about your life"},
                {"role": "user", "content": f'Act as if you are an {common_name}. Write a description (max {return_word_count} words) of your life based on the following text:\n\n "{wiki_text}"\n.'}
            ],
            'temperature': 0.2,
            'max_tokens' : 250,
            'presence_penalty' : 1.0,
            'frequency_penalty' : 1.0
        }

        # Asynchronously generate description
        max_retries = 10
        retry_delay = 0.5

        for i in range(max_retries):
            try:
                response = await openai.ChatCompletion.acreate(**params)
                break  # exit the loop if the request succeeds
            except Exception as e:
                print(e)
                await asyncio.sleep(retry_delay * (i+1))  # sleep for longer each time
        else:
            response = None  # all retries failed
            print(f"no response from openai for id {species_id}: {common_name}")
            return

        if response:
            description = response.choices[0].message.content
            print(f"generated description for id {species_id}: {common_name}")

    if not wiki_text: print(f"no wiki found for id  {species_id} : {common_name}")

    species['description'] = description


async def main():
    filename = 'species.json'
    
    # Read species from json file
    species_list = read_species_from_file(filename)

    # Process each species
    tasks = [coroutine_for_getting_and_writing_description(species) for species in species_list]

    # Run all tasks concurrently
    await asyncio.gather(*tasks)

    # Update the JSON file with the descriptions
    write_species_to_file(filename, species_list)


if __name__ == "__main__":
    asyncio.run(main())
