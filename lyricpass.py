#!/usr/bin/env python3

"""
Utility to scrape lyrics from https://lyrics.com

Usage:
lyricspass.py -a <artist>
lyricpass.py -i <file with multiple artists>

Example:
python lyricpass.py -a "Rob Zombie"
python lyricpass.py -i /tmp/artists.txt

Outputs two files:
raw-lyrics.txt <everything>
wordlist.txt <cleaned passphrases>

Original tool by initstring. If you're into cracking complex passwords, check out
github.com/initstring/passphrase-wordlist for more fun!
"""

from functools import partial
from time import sleep
from multiprocessing import Queue
import multiprocessing
import textwrap
import argparse
import urllib.request
import datetime
import os
import sys
import re
import unicodedata

SITE = "https://www.lyrics.com/"
HEADER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/119.0"
}
QUEUE_SENTINEL = "ENQUEUE"


def parse_args():
    """
    Handle user-passed parameters
    """
    desc = "Scrape song lyrics from wikia.com"
    parser = argparse.ArgumentParser(description=desc)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-a", "--artist", type=str, action="store", help="Single artist to scrape"
    )
    group.add_argument(
        "-i",
        "--infile",
        type=str,
        action="store",
        help="File containing one artist per line to scrape",
    )

    parser.add_argument(
        "--min", type=int, default=8, help="Minimum passphrase length. Default=8"
    )
    parser.add_argument(
        "--max", type=int, default=40, help="Minimum passphrase length. Default=40"
    )
    parser.add_argument(
        "--max-concurrent-dl",
        type=int,
        default=50,
        help="Max number of pool procs at once. Default=50",
    )

    args = parser.parse_args()

    if args.infile:
        if not os.access(args.infile, os.R_OK):
            print("[!] Cannot access input file, exiting")
            sys.exit()

    return args


def remove_accents(input_str: str) -> str:
    nfkd_form = unicodedata.normalize("NFKD", input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def make_phrases(line: str, args: argparse.Namespace) -> list[str]:
    """
    Cleans raw lyrics into usable passphrases
    """
    clean_lines: list[str] = []
    final_lines: list[str] = []

    # Allow only letters, numbers, spaces, and some punctuation
    allowed_chars = re.compile("[^a-zA-Z0-9 '&]")

    # Lowercase everything, deal with common punctuation, and try our best to replace
    # any diacritics
    line = allowed_chars.sub("", remove_accents(re.sub(r"[-_]", " ", line.lower())))

    # Shrinks down multiple spaces
    line = re.sub(r"\s\s+", " ", line)

    # If line has an apostrophe make a duplicate without
    #if "'" in line:
    #    clean_lines.append(re.sub("'", "", line))

    ## Making duplicating phrases including and / &
    #if " and " in line:
    #    clean_lines.append(re.sub(" and ", " & ", line))

    #if "&" in line:
    #    newline = re.sub("&", " and ", line)
    #    newline = re.sub(r"\s+", " ", newline).strip()
    #    clean_lines.append(newline)

    # Add what is left to the list
    clean_lines.append(line)

    # Only keep items in the acceptable length
    for item in clean_lines:
        line_length = len(item)

        if line_length < args.min:
            continue
        if line_length > args.max:
            for l in textwrap.wrap(item, args.max, break_long_words=False):
                print(f"Adding: {l}")
                final_lines.append(l)
        else:
            final_lines.append(item)

    return final_lines


def parse_artists(args: argparse.Namespace) -> list[str]:
    """
    Return a list of song artists for parsing
    """
    whitelist = re.compile("[^a-zA-Z0-9-+]")
    artists: list[str] = []

    if args.artist:
        raw_artists = [
            args.artist,
        ]
    else:
        with open(args.infile, encoding="utf-8", errors="ignore") as infile:
            raw_artists = infile.readlines()

    for artist in raw_artists:
        artist = artist.replace(" ", "+")
        artist = whitelist.sub("", artist)
        if artist not in artists:
            artists.append(artist)

    return artists


def build_urls(artist: str) -> list[str]:
    """
    Creates a list of song URLs for a specific artist
    """
    not_found = "We couldn't find any artists matching your query"
    query_url = f"{SITE}/artist.php?name={artist}"
    song_ids = []
    regex = re.compile(r'href="/lyric/(.*?)/')

    req = urllib.request.Request(query_url, headers=HEADER)
    with urllib.request.urlopen(req) as response:
        html = response.read().decode()

    # The songs are stored by a unique ID
    song_ids = re.findall(regex, html)

    if not_found in html:
        print("[!] Artist {} not found, skipping".format(artist))

        # Clear out the "suggested" songs it finds in this scenario
        song_ids = []
    elif not song_ids:
        print("[!] No songs found for {}, skipping".format(artist))
    else:
        print("[+] Found {} songs for artists {}".format(len(song_ids), artist))

    # The "print" URL shows us the easiest to decode version of the song
    url_list = [SITE + "db-print.php?id=" + id for id in song_ids]

    return url_list


def write_data(outfile: str, data: list[str]) -> None:
    """
    Generic helper function to write text to a file
    """
    with open(outfile, "a") as open_file:
        for line in data:
            if line:
                open_file.write(f"{line}\n")


def lyric_writer(lyric_file: str, lyrics_queue: Queue, max_concurrent: int) -> None:
    finished = 0
    while True:
        lyrics: list[str] = lyrics_queue.get()
        if lyrics == QUEUE_SENTINEL:  # type: ignore
            finished += 1

            if finished == max_concurrent:
                break

        else:
            write_data(lyric_file, lyrics)


def get_song_lyrics(queues: tuple[Queue, Queue]) -> None:
    input_queue: Queue = queues[0]
    lyrics_queue: Queue = queues[1]

    while True:
        url: str = input_queue.get()

        if url == QUEUE_SENTINEL:
            input_queue.task_done()
            lyrics_queue.put(QUEUE_SENTINEL)
            break

        try:
            print(f"[+] Fetching {url} lyrics...")
            regex = re.compile(r"<pre.*?>(.*?)</pre>", re.DOTALL)
            newline = re.compile(r"\r\n|\n")

            req = urllib.request.Request(url, headers=HEADER)
            with urllib.request.urlopen(req) as response:
                html = response.read().decode()

            lyrics = re.findall(regex, html)

            # We should always have a match... but if not, skip this url
            if not lyrics:
                print("\n[!] Found no lyrics at {}".format(url))
                continue

            lyrics_queue.put(re.split(newline, lyrics[0]))
        finally:
            input_queue.task_done()


def producer(input_q: Queue, urls: list[str], n_consumers: int) -> None:
    """."""
    for url in urls:
        while input_q.full():
            sleep(1)

        print(f"[+] Putting {url} in queue from producer")
        input_q.put(url)

    for _ in range(n_consumers):
        while input_q.full():
            sleep(1)

        input_q.put(QUEUE_SENTINEL)


def scrape_lyrics(
    url_list: list[str], lyric_file: str, max_concurrent_dl: int
) -> set[str]:
    """
    Scrapes raw lyric data from a list of URLs
    """

    manager = multiprocessing.Manager()
    lyrics_queue = manager.Queue()
    input_queue = manager.Queue(int(max_concurrent_dl * 1.5))

    with multiprocessing.Pool() as pool:
        p = partial(producer, input_queue, url_list, max_concurrent_dl)
        w = partial(get_song_lyrics)
        c = partial(lyric_writer, lyric_file, lyrics_queue, max_concurrent_dl)
        pool.apply_async(p)
        pool.apply_async(c)
        pool.map(w, [(input_queue, lyrics_queue) for _ in range(max_concurrent_dl)])
        input_queue.join()
        pool.close()
        pool.join()


def main():
    """
    Main program function
    """
    args = parse_args()
    artists: list[str] = parse_artists(args)
    artists_str = "-".join(artists)

    now = datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")

    lyric_file = f"raw-lyrics-{artists_str}-{now}"
    PASS_FILE = f"wordlist-{artists_str}-{now}"

    final_phrases = []

    # First, we grab all the lyrics for a given artist.
    # The scrape_lyrics function will write the raw lyrics to an output
    # file as it goes, which may come in handy if the program exits early
    # due to an error.
    for artist in artists:
        print("[+] Looking up artist {}".format(artist))
        url_list = build_urls(artist)
        if not url_list:
            continue

        scrape_lyrics(url_list, lyric_file, args.max_concurrent_dl)

    with open(lyric_file) as infile:
        raw_words = infile.readlines()

    final_phrases = [
        phrase for lyric in raw_words for phrase in make_phrases(lyric, args)
    ]

    ### Write out the somewhat cleaned passphrases to a file
    write_data(PASS_FILE, final_phrases)

    print("[+] All done!\n")
    print(f"Raw lyrics: {lyric_file}")
    print(f"Passphrases: {PASS_FILE}")


if __name__ == "__main__":
    main()
