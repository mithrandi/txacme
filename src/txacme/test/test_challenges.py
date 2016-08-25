"""
Tests for `txacme.challenges`.
"""
from acme import challenges
from acme.jose import b64encode
from hypothesis import strategies as s
from hypothesis import example, given
from testtools import TestCase
from testtools.matchers import (
    Always, Contains, EndsWith, Equals, HasLength, Is, IsInstance, MatchesAll,
    MatchesListwise, MatchesPredicate, MatchesStructure, Not)
from testtools.twistedsupport import succeeded
from twisted.internet.defer import execute, maybeDeferred
from zope.interface.verify import verifyObject

from txacme.challenges import LibcloudDNSResponder, TLSSNI01Responder
from txacme.challenges._tls import _MergingMappingProxy
from txacme.errors import NotInZone, ZoneNotFound
from txacme.interfaces import IResponder
from txacme.test import strategies as ts
from txacme.test.test_client import failed_with, RSA_KEY_512, RSA_KEY_512_RAW

# A random example token for the challenge tests that need one
EXAMPLE_TOKEN = b'BWYcfxzmOha7-7LoxziqPZIUr99BCz3BfbN9kzSFnrU'


class _CommonResponderTests(object):
    """
    Common properties which every responder implementation should satisfy.
    """
    def test_interface(self):
        """
        The `.IResponder` interface is correctly implemented.
        """
        responder = self._responder_factory()
        verifyObject(IResponder, responder)
        self.assertThat(responder.challenge_type, Equals(self._challenge_type))

    @example(token=EXAMPLE_TOKEN)
    @given(token=s.binary(min_size=32, max_size=32).map(b64encode))
    def test_stop_responding_already_stopped(self, token):
        """
        Calling ``stop_responding`` when we are not responding for a server
        name does nothing.
        """
        challenge = self._challenge_factory(token=token)
        response = challenge.response(RSA_KEY_512)
        responder = self._responder_factory()
        self.assertThat(
            maybeDeferred(
                responder.stop_responding,
                u'example.com',
                challenge,
                response),
            succeeded(Always()))


class TLSResponderTests(_CommonResponderTests, TestCase):
    """
    `.TLSSNI01Responder` is a responder for tls-sni-01 challenges that works
    with txsni.
    """
    _challenge_factory = challenges.TLSSNI01
    _responder_factory = TLSSNI01Responder
    _challenge_type = u'tls-sni-01'

    @example(token=b'BWYcfxzmOha7-7LoxziqPZIUr99BCz3BfbN9kzSFnrU')
    @given(token=s.binary(min_size=32, max_size=32).map(b64encode))
    def test_start_responding(self, token):
        """
        Calling ``start_responding`` makes an appropriate entry appear in the
        host map.
        """
        ckey = RSA_KEY_512_RAW
        challenge = challenges.TLSSNI01(token=token)
        response = challenge.response(RSA_KEY_512)
        server_name = response.z_domain.decode('ascii')
        host_map = {}
        responder = TLSSNI01Responder()
        responder._generate_private_key = lambda key_type: ckey
        wrapped_host_map = responder.wrap_host_map(host_map)

        self.assertThat(wrapped_host_map, Not(Contains(server_name)))
        responder.start_responding(u'example.com', challenge, response)
        self.assertThat(
            wrapped_host_map.get(server_name.encode('utf-8')).certificate,
            MatchesPredicate(response.verify_cert, '%r does not verify'))

        # Starting twice before stopping doesn't break things
        responder.start_responding(u'example.com', challenge, response)
        self.assertThat(
            wrapped_host_map.get(server_name.encode('utf-8')).certificate,
            MatchesPredicate(response.verify_cert, '%r does not verify'))

        responder.stop_responding(u'example.com', challenge, response)
        self.assertThat(wrapped_host_map, Not(Contains(server_name)))


class MergingProxyTests(TestCase):
    """
    ``_MergingMappingProxy`` merges two mappings together.
    """
    @example(underlay={}, overlay={}, key=u'foo')
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)),
           key=s.text())
    def test_get_overlay(self, underlay, overlay, key):
        """
        Getting an key that only exists in the overlay returns the value from
        the overlay.
        """
        underlay.pop(key, None)
        overlay[key] = object()
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        self.assertThat(proxy[key], Is(overlay[key]))

    @example(underlay={}, overlay={}, key=u'foo')
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)),
           key=s.text())
    def test_get_underlay(self, underlay, overlay, key):
        """
        Getting an key that only exists in the underlay returns the value from
        the underlay.
        """
        underlay[key] = object()
        overlay.pop(key, None)
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        self.assertThat(proxy[key], Is(underlay[key]))

    @example(underlay={}, overlay={}, key=u'foo')
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)),
           key=s.text())
    def test_get_both(self, underlay, overlay, key):
        """
        Getting an key that exists in both the underlay and the overlay returns
        the value from the overlay.
        """
        underlay[key] = object()
        overlay[key] = object()
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        self.assertThat(proxy[key], Not(Is(underlay[key])))
        self.assertThat(proxy[key], Is(overlay[key]))

    @example(underlay={u'foo': object(), u'bar': object()},
             overlay={u'bar': object(), u'baz': object()})
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)))
    def test_len(self, underlay, overlay):
        """
        ``__len__`` of the proxy does not count duplicates.
        """
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        self.assertThat(len(proxy), Equals(len(list(proxy))))

    @example(underlay={u'foo': object(), u'bar': object()},
             overlay={u'bar': object(), u'baz': object()})
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)))
    def test_iter(self, underlay, overlay):
        """
        ``__iter__`` of the proxy does not produce duplicate keys.
        """
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        keys = sorted(list(proxy))
        self.assertThat(keys, Equals(sorted(list(set(keys)))))

    @example(underlay={u'foo': object()}, overlay={}, key=u'foo')
    @example(underlay={}, overlay={}, key=u'bar')
    @given(underlay=s.dictionaries(s.text(), s.builds(object)),
           overlay=s.dictionaries(s.text(), s.builds(object)),
           key=s.text())
    def test_contains(self, underlay, overlay, key):
        """
        The mapping only contains a key if it can be gotten.
        """
        proxy = _MergingMappingProxy(
            overlay=overlay, underlay=underlay)
        self.assertThat(
            key in proxy,
            Equals(proxy.get(key) is not None))


class LibcloudResponderTests(_CommonResponderTests, TestCase):
    """
    `.LibcloudDNSResponder` implements a responder for dns-01 challenges using
    libcloud on the backend.
    """
    _challenge_factory = challenges.DNS01
    _challenge_type = u'dns-01'

    def _responder_factory(self, zone_name=u'example.com'):
        responder = LibcloudDNSResponder.create(
            reactor=None,
            driver_name='dummy',
            username='ignored',
            password='ignored',
            zone_name=zone_name,
            settle_delay=0.0)
        responder._driver.create_zone(zone_name)
        responder._ensure_thread_pool_started = lambda: None
        responder._defer = execute
        return responder

    @example(token=EXAMPLE_TOKEN,
             subdomain=u'acme-testing',
             zone_name=u'example.com')
    @given(token=s.binary(min_size=32, max_size=32).map(b64encode),
           subdomain=ts.dns_names(),
           zone_name=ts.dns_names())
    def test_start_responding(self, token, subdomain, zone_name):
        """
        Calling ``start_responding`` causes an appropriate TXT record to be
        created.
        """
        challenge = self._challenge_factory(token=token)
        response = challenge.response(RSA_KEY_512)
        responder = self._responder_factory(zone_name=zone_name)
        server_name = u'{}.{}'.format(subdomain, zone_name)
        zone = responder._driver.list_zones()[0]

        self.assertThat(zone.list_records(), HasLength(0))
        self.assertThat(
            responder.start_responding(server_name, challenge, response),
            succeeded(Always()))
        self.assertThat(
            zone.list_records(),
            MatchesListwise([
                MatchesStructure(
                    name=EndsWith(subdomain),
                    type=Equals('TXT'),
                    )]))

        # Starting twice before stopping doesn't break things
        self.assertThat(
            responder.start_responding(server_name, challenge, response),
            succeeded(Always()))
        self.assertThat(zone.list_records(), HasLength(1))

        self.assertThat(
            responder.stop_responding(server_name, challenge, response),
            succeeded(Always()))
        self.assertThat(zone.list_records(), HasLength(0))

    @example(token=EXAMPLE_TOKEN,
             subdomain=u'acme-testing',
             zone_name=u'example.com')
    @given(token=s.binary(min_size=32, max_size=32).map(b64encode),
           subdomain=ts.dns_names(),
           zone_name=ts.dns_names())
    def test_wrong_zone(self, token, subdomain, zone_name):
        """
        Trying to respond for a domain not in the configured zone results in a
        `.NotInZone` exception.
        """
        challenge = self._challenge_factory(token=token)
        response = challenge.response(RSA_KEY_512)
        responder = self._responder_factory(zone_name=zone_name)
        server_name = u'{}.{}.junk'.format(subdomain, zone_name)
        self.assertThat(
            maybeDeferred(
                responder.start_responding, server_name, challenge, response),
            failed_with(MatchesAll(
                IsInstance(NotInZone),
                MatchesStructure(
                    server_name=EndsWith(server_name),
                    zone_name=Equals(zone_name)))))

    @example(token=EXAMPLE_TOKEN,
             subdomain=u'acme-testing',
             zone_name=u'example.com')
    @given(token=s.binary(min_size=32, max_size=32).map(b64encode),
           subdomain=ts.dns_names(),
           zone_name=ts.dns_names())
    def test_missing_zone(self, token, subdomain, zone_name):
        """
        `.ZoneNotFound` is raised if the configured zone cannot be found at the
        configured provider.
        """
        challenge = self._challenge_factory(token=token)
        response = challenge.response(RSA_KEY_512)
        responder = self._responder_factory(zone_name=zone_name)
        server_name = u'{}.{}'.format(subdomain, zone_name)
        for zone in responder._driver.list_zones():
            zone.delete()
        self.assertThat(
            maybeDeferred(
                responder.start_responding, server_name, challenge, response),
            failed_with(MatchesAll(
                IsInstance(ZoneNotFound),
                MatchesStructure(
                    zone_name=Equals(zone_name)))))


__all__ = ['TLSResponderTests', 'MergingProxyTests', 'LibcloudResponderTests']
