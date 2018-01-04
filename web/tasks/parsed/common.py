import re
from lxml import etree

from app import app


########################################################################################################################


def format_parsed_response(action, params, results=None, errors=None, succeeded=None):
    if succeeded is None:
        succeeded = True if errors else False

    return {
        'action': action,
        'params': params,
        'results': results if results is not None else {},
        'errors': errors if errors is not None else {},
        'succeeded': succeeded
    }


########################################################################################################################


class AmzXmlResponse:
    """A utility class for dealing with Amazon's XML responses."""

    def __init__(self, xml=None):
        self._xml = None
        self.tree = None

        self.xml = xml

    @property
    def xml(self):
        return self._xml

    @xml.setter
    def xml(self, xml):
        """Perform automatic etree parsing."""
        self._xml, self.tree = None, None

        if xml is not None:
            self._xml = self.remove_namespaces(xml)
            self.tree = etree.fromstring(self._xml)

    @staticmethod
    def remove_namespaces(xml):
        """Remove all traces of namespaces from the given XML string."""
        re_ns_decl = re.compile(r' xmlns(:\w*)?="[^"]*"', re.IGNORECASE)
        re_ns_open = re.compile(r'<\w+:')
        re_ns_close = re.compile(r'/\w+:')

        response = re_ns_decl.sub('', xml)  # Remove namespace declarations
        response = re_ns_open.sub('<', response)  # Remove namespaces in opening tags
        response = re_ns_close.sub('/', response)  # Remove namespaces in closing tags
        return response

    def xpath_get(self, path, root_tag=None, _type=str, default=None):
        """Utility method for getting data values from XPath selectors."""
        tag = root_tag if root_tag is not None else self.tree
        try:
            data = tag.xpath(path)[0].text
            if _type is str and data is None:
                raise TypeError
            else:
                return _type(data)
        except (IndexError, ValueError, TypeError):
            return default

    @property
    def error_code(self):
        """Holds the error code if the response was an error, otherwise None."""
        if self.tree is None:
            return None

        return self.xpath_get('/ErrorResponse/Error/Code')

    @property
    def error_message(self):
        """Holds the error message if the response was an error, otherwise None."""
        if self.tree is None:
            return None

        return self.xpath_get('/ErrorResponse/Error/Message')

    @property
    def request_id(self):
        """Returns the RequestID parameter."""
        if self.tree is None:
            return None

        return self.xpath_get('//RequestID')

    def error_as_json(self):
        """Formats an error response as a simple JSON object."""
        return {
            'error': {
                'code': self.error_code,
                'message': self.error_message,
                'request_id': self.request_id
            }
        }