#-------------------------------------------------------------------------------
# Name:         sfp_bingsearch
# Purpose:      Searches Bing for content related to the domain in question.
#
# Author:      Steve Micallef <steve@binarypool.com>
#
# Created:     06/10/2013
# Copyright:   (c) Steve Micallef 2013
# Licence:     GPL
#-------------------------------------------------------------------------------

import sys
import random
import re
import time
from sflib import SpiderFoot, SpiderFootPlugin, SpiderFootEvent

# SpiderFoot standard lib (must be initialized in setup)
sf = None

class sfp_bingsearch(SpiderFootPlugin):
    """Bing:Some light Bing scraping to identify sub-domains and co-hosted sites."""

    # Default options
    opts = {
        'fetchlinks':   True,   # Should we fetch links on the base domain?
        'pages':        20,      # Number of bing results pages to iterate
        'cohostsamedomain': False
    }

    # Option descriptions
    optdescs = {
        'fetchlinks': "Fetch links found on the target domain-name?",
        'pages':    "Number of Bing results pages to iterate through.",
        'cohostsamedomain': "Treat co-hosted sites on the same target domain as co-hosting?"
    }

    # Target
    baseDomain = None
    results = list()

    def setup(self, sfc, target, userOpts=dict()):
        global sf

        sf = sfc
        self.baseDomain = target
        self.results = list()

        for opt in userOpts.keys():
            self.opts[opt] = userOpts[opt]

    # What events is this module interested in for input
    def watchedEvents(self):
        return [ "IP_ADDRESS" ]

    # What events this module produces
    # This is to support the end user in selecting modules based on events
    # produced.
    def producedEvents(self):
        return [ "LINKED_URL_INTERNAL", "SEARCH_ENGINE_WEB_CONTENT", 
            "CO_HOSTED_SITE" ]

    # Handle events sent to this module
    def handleEvent(self, event):
        eventName = event.eventType
        srcModuleName = event.module
        eventData = event.data
        self.currentEventSrc = event

        sf.debug("Received event, " + eventName + ", from " + srcModuleName)

        # Don't look up stuff twice
        if eventData in self.results:
            sf.debug("Skipping " + eventData + " as already mapped.")
            return None
        else:
            self.results.append(eventData)

        results = self.bingIterate("ip:" + eventData, dict(limit=self.opts['pages']))
        myres = list()
        if results == None:
            sf.info("No data returned from Bing.")
            return None

        for key in results.keys():
            res = results[key]
            matches = re.findall("<div class=\"sb_meta\"><cite>(\S+)</cite>", 
                res, re.IGNORECASE)
            for match in matches:
                sf.info("Found something on same IP: " + match)
                site = sf.urlFQDN(match)
                if site not in myres and site != eventData:
                    if not self.opts['cohostsamedomain'] and site.endswith(self.baseDomain):
                        sf.debug("Skipping " + site + " because it is on the same domain.")
                        continue
                    evt = SpiderFootEvent("CO_HOSTED_SITE", site, self.__name__, event)
                    self.notifyListeners(evt)
                    myres.append(site)

            # Submit the bing results for analysis
            evt = SpiderFootEvent("SEARCH_ENGINE_WEB_CONTENT", results[key], 
                self.__name__, event)
            self.notifyListeners(evt)

    # Scrape Bing for content, starting at startUrl and iterating through
    # results based on options supplied. Will return a dictionary of all pages
    # fetched and their contents {page => content}.
    # Options accepted:
    # limit: number of search result pages before returning, default is 10
    # nopause: don't randomly pause between fetches
    def bingIterate(self, searchString, opts=dict()):
        limit = 10
        fetches = 0
        returnResults = dict()

        if opts.has_key('limit'):
            limit = opts['limit']

        # We attempt to make the URL look as authentically human as possible
        seedUrl = "http://www.bing.com/search?q={0}".format(searchString) + \
            "&pc=MOZI"
        firstPage = sf.fetchUrl(seedUrl, timeout=self.opts['_fetchtimeout'],
            useragent=self.opts['_useragent'])
        if firstPage['code'] == "403":
            sf.error("Bing doesn't like us right now..", False)
            return None

        if firstPage['content'] == None:
            sf.error("Failed to fetch content from Bing.", False)
            return None

        returnResults[seedUrl] = firstPage['content']

        matches = re.findall("(\/search\S+first=\d+.[^\'\"]*FORM=\S+)", firstPage['content'])
        while matches > 0 and fetches < limit:
            nextUrl = None
            fetches += 1
            for match in matches:
                # Bing moves in increments of 10
                if "first=" + str((fetches*10)+1) in match:
                    nextUrl = match.replace("&amp;", "&").replace("%3a", ":")

            if nextUrl == None:
                sf.debug("Nothing left to scan for in Bing results.")
                return returnResults
            sf.info("Next Bing URL: " + nextUrl)

            # Wait for a random number of seconds between fetches
            if not opts.has_key('nopause'):
                pauseSecs = random.randint(4, 15)
                sf.info("Pausing for " + str(pauseSecs))
                time.sleep(pauseSecs)

            # Check if we've been asked to stop
            if self.checkForStop():
                return None

            nextPage = sf.fetchUrl('http://www.bing.com' + nextUrl,
                timeout=self.opts['_fetchtimeout'], useragent=self.opts['_useragent'])
            if firstPage['code'] == 403:
                sf.error("Bing doesn't like us any more..", False)
                return returnResults

            if nextPage['content'] == None:
                sf.error("Failed to fetch subsequent content from Bing.", False)
                return returnResults

            returnResults[nextUrl] = nextPage['content']
            matches = re.findall("(\/search\S+first=\d+.[^\'\"]*)", nextPage['content'], re.IGNORECASE)

        return returnResults

    def start(self):
        # Sites hosted on the domain
        pages = self.bingIterate("domain:" + self.baseDomain, dict(limit=self.opts['pages']))
        if pages == None:
            sf.info("No results returned from Bing.")
            return None

        for page in pages.keys():
            if page in self.results:
                continue
            else:
                self.results.append(page)

            # Check if we've been asked to stop
            if self.checkForStop():
                return None

            # Submit the bing results for analysis
            evt = SpiderFootEvent("SEARCH_ENGINE_WEB_CONTENT", pages[page], self.__name__)
            self.notifyListeners(evt)

            # We can optionally fetch links to our domain found in the search
            # results. These may not have been identified through spidering.
            if self.opts['fetchlinks']:
                links = sf.parseLinks(page, pages[page], self.baseDomain)
                if len(links) == 0:
                    continue

                for link in links:
                    if link in self.results:
                        continue
                    else:
                        self.results.append(link)
                    if sf.urlBaseUrl(link).endswith(self.baseDomain):
                        sf.debug("Found a link: " + link)
                        if self.checkForStop():
                            return None

                        evt = SpiderFootEvent("LINKED_URL_INTERNAL", link, self.__name__)
                        self.notifyListeners(evt)

# End of sfp_bingsearch class
