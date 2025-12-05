import re
from typing import Set


class KeywordFilter:
    def __init__(self):
        self._pattern = re.compile(r'[\W_ ]', re.UNICODE)

    def normalize(self, text: str) -> str:
        return self._pattern.sub('', text).lower()

    def contains_banned(self, query: str, banned_keywords: Set[str]) -> bool:
        normalized_query = self.normalize(query)
        for keyword in banned_keywords:
            normalized_keyword = self.normalize(keyword)
            if normalized_keyword and normalized_keyword in normalized_query:
                return True
        return False


keyword_filter = KeywordFilter()
