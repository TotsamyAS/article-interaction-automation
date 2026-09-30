"""Display vocabulary; persisted field keys, dataset and canonical answers stay stable."""
import re

WORDING_VERSION = 'ru-project-terms-v2'
FIELD_ALIASES = {'эпик': 'направление работ', 'спринт': 'рабочий цикл'}


def display_text(text: str) -> str:
    replacements = {'эпикам': 'направлениям работ', 'эпике': 'направлении работ', 'эпик': 'направление работ',
                    'спринтам': 'рабочим циклам', 'спринте': 'рабочем цикле', 'спринт': 'рабочий цикл'}
    def replace(match):
        word = match.group()
        value = replacements[word.casefold()]
        return value.capitalize() if word[0].isupper() else value
    return re.sub(r'\b(?:эпикам|эпике|эпик|спринтам|спринте|спринт)\b', replace, text, flags=re.IGNORECASE)


def stored_value(field: str, value: str) -> str:
    return re.sub(r'^рабочий цикл\s+', 'Спринт ', value, flags=re.IGNORECASE) if field == 'sprint' else value
