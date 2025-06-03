from django import template

register = template.Library()

@register.filter
def lookup(dictionary, key):
    return dictionary.get(key, 0)  # Default to 0 if key not found