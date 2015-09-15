"""SublimeSalesforceReference: Quick access to Salesforce Documentation from Sublime Text"""
__version__ = "1.5.0"
__author__ = "James Hill <me@jameshill.io>"
__copyright__ = "SublimeSalesforceReference: (C) 2014-2015 James Hill. GNU GPL 3."
__credits__ = ["All Salesforce Documentation is © Copyright 2000–2015 salesforce.com, inc.", "ThreadProgress.py is under the MIT License, Will Bond <will@wbond.net>, and SalesforceReference.py's RetrieveIndexThread method is a derives in part from code under the same license"]

import sublime, sublime_plugin
import urllib.request
import webbrowser
import threading
from queue import Queue
import re
# TODO: See if possible to rename the plugin while playing nice with Package
#       Control. The current name is "sublime-salesforce-reference" - which
#       means we can't do (for example)
#       `import sublime-salesforce-reference.salesforce_reference.cache`
#       as the dashes are interpreted as minuses
from .salesforce_reference.cache import SalesforceReferenceCache,SalesforceReferenceCacheEntry
from .salesforce_reference.retrieve import DocTypeEnum, DocType
from .ThreadProgress import ThreadProgress

# Import BeautifulSoup (scraping library) and html.parser
#  - Necessary, because as at 2015-06-02 Salesforce no longer uses an XML file
#    for generating Table of Contents, so we have to scrape a ToC out of the
#    page itself
#  - NB: attempting to use html5lib was horrible, due to dependence on six,
#    which obstinately refused to work.
#  - Built in html.parser seems perfectly adequate to needs, but if we ever
#    support ST2, we should check for ST2 and use HTMLParser (if BeautifulSoup
#    supports)
#  - NB: bs4 was rebuilt (using 2to3) for Python3; we'd need to include a
#    Python2 build if we ever support ST2
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), os.path.normpath("lib")))
from bs4 import BeautifulSoup
import html.parser


#Store all reference entries
reference_cache = SalesforceReferenceCache()

def plugin_loaded():
    # Get settings
    global settings
    settings = sublime.load_settings("SublimeSalesforceReference.sublime-settings")
    if settings != None and settings.get("refreshCacheOnLoad") == True:
        thread = RetrieveIndexThread(sublime.active_window(), "*", open_when_done=False,sublime_opening_cache_refresh=True)
        thread.start()
        ThreadProgress(thread, "Retrieving Salesforce Reference Index...", "")

# Command to retrieve Apex reference
class SalesforceReferenceApexCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = RetrieveIndexThread(self.window, DocTypeEnum.APEX)
        thread.start()
        ThreadProgress(thread, "Retrieving Salesforce Apex Reference Index...", "")

# Command to retrieve Visualforce reference
class SalesforceReferenceVisualforceCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = RetrieveIndexThread(self.window, DocTypeEnum.VISUALFORCE)
        thread.start()
        ThreadProgress(thread, "Retrieving Salesforce Visualforce Reference Index...", "")

# Command to retrieve Service Console reference
class SalesforceReferenceServiceConsoleCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = RetrieveIndexThread(self.window, DocTypeEnum.SERVICECONSOLE)
        thread.start()
        ThreadProgress(thread, "Retrieving Salesforce Service Console Reference Index...", "")

# Command to retrieve all references specified in settings file
class SalesforceReferenceAllDocumentationTypesCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = RetrieveIndexThread(self.window, "*")
        thread.start()
        ThreadProgress(thread, "Retrieving Salesforce Reference Index...", "")

# Thread that actually download specified reference
class RetrieveIndexThread(threading.Thread):
    """
    A thread to run retrieval of the Saleforce Documentation index, or access the reference_cache
    """

    def __init__(self, window, doc_type, open_when_done=True, sublime_opening_cache_refresh=False):
        """
        :param window:
            An instance of :class:`sublime.Window` that represents the Sublime
            Text window to show the available package list in.
        :param doc_type:
            Either:
             - a salesforce_reference.retrieve.DocType (usually obtained from
                salesforce_reference.retrieve.DocTypeEnum) indicating the
                docType to retrieve
             - the string "*" - indicating that all documentation types should
                be retrieved and displayed at the same time
        :param open_when_done:
            Whether this thread is being run solely for caching, or should open
            the documentation list for user selection when done. Defaults to
            true - should open the documentaton list when done
        :param sublime_opening_cache_refresh:
            flag indicating whether this is a cache refresh happening while
            sublime is opening. Setting this to True causes special behaviour:
             - open_when_done param will be set to False as this is not a user
                initiated action, so opening the doc searcher would be jarring
             - doc_type will be ignored - we will cache all the doc types we can,
                however...
             - if refreshCacheOnLoad is set to False in the settings for a
                particular doc type, this doc type will not be cached
        """
        self.window = window
        if not isinstance(doc_type,DocType) and doc_type != "*":
            raise ValueError(
                    "doc_type parameter to RetrieveIndexThread must be either "
                    "an instance of DocType (or a subclass), or the string '*' "
                    "indicating you want to retrieve and display all available "
                    "documentation types "
                )
        self.doc_type = doc_type
        self.open_when_done = open_when_done
        self.sublime_opening_cache_refresh = sublime_opening_cache_refresh
        if sublime_opening_cache_refresh:
            self.doc_type = "*"
            self.open_when_done = False
        self.queue = Queue()
        global reference_cache
        threading.Thread.__init__(self)

    def run(self):
        cache_lock = threading.Lock()

        if self.doc_type == "*":
            for doc_type in DocTypeEnum.get_all():
                doc_type_settings = settings.get("docTypes").get(doc_type.name.lower())
                if  (
                            not doc_type_settings.get("excludeFromAllDocumentationCommand")
                        and not reference_cache.entries_by_doc_type.get(doc_type.name)
                        and not (
                                        self.sublime_opening_cache_refresh
                                    and not doc_type_settings.get("refreshCacheOnLoad")
                                )
                    ):
                    self.queue.put(doc_type.preferred_strategy(self.window,reference_cache,cache_lock,self.queue.task_done))
        else:
            if not reference_cache.entries_by_doc_type.get(self.doc_type.name):
                self.queue.put(self.doc_type.preferred_strategy(self.window,reference_cache,cache_lock,self.queue.task_done))

        while not self.queue.empty():
            self.queue.get().start()

        if(self.open_when_done):
            self.queue.join()
            if self.doc_type == "*":
                self.window.show_quick_panel(reference_cache.titles, self.open_documentation)
            else:
                self.window.show_quick_panel(reference_cache.titles_by_doc_type.get(self.doc_type.name), self.open_documentation)

    def open_documentation(self, reference_index):
        url = ""
        if(reference_index != -1):
            if self.doc_type == "*":
                entry = reference_cache[reference_index]
            else:
                entry = reference_cache.entries_by_doc_type.get(self.doc_type.name)[reference_index]

            if entry:
                url = self.doc_type.url + entry.url

            if url:
                webbrowser.open_new_tab(url)
