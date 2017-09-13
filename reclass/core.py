#
# -*- coding: utf-8 -*-
#
# This file is part of reclass (http://github.com/madduck/reclass)
#
# Copyright © 2007–14 martin f. krafft <madduck@madduck.net>
# Released under the terms of the Artistic Licence 2.0
#

import copy
import time
#import types
import re
import sys
import fnmatch
import shlex
import string
import yaml
from reclass.output.yaml_outputter import ExplicitDumper
from reclass.datatypes import Entity, Classes, Parameters
from reclass.errors import MappingFormatError, ClassNotFound
from reclass.defaults import AUTOMATIC_RECLASS_PARAMETERS

class Core(object):

    def __init__(self, storage, class_mappings, input_data=None,
            ignore_class_notfound=False, ignore_class_regexp=['*']):
        self._storage = storage
        self._class_mappings = class_mappings
        self._ignore_class_notfound = ignore_class_notfound
        self._input_data = input_data

        if type(ignore_class_regexp) == type(''):
          self._ignore_class_regexp = [ignore_class_regexp]
        else:
          self._ignore_class_regexp = ignore_class_regexp

    @staticmethod
    def _get_timestamp():
        return time.strftime('%c')

    @staticmethod
    def _match_regexp(key, nodename):
        return re.search(key, nodename)

    @staticmethod
    def _match_glob(key, nodename):
        return fnmatch.fnmatchcase(nodename, key)

    @staticmethod
    def _shlex_split(instr):
        lexer = shlex.shlex(instr, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        regexp = False
        if instr[0] == '/':
            lexer.quotes += '/'
            lexer.escapedquotes += '/'
            regexp = True
        try:
            key = lexer.get_token()
        except ValueError, e:
            raise MappingFormatError('Error in mapping "{0}": missing closing '
                                     'quote (or slash)'.format(instr))
        if regexp:
            key = '/{0}/'.format(key)
        return key, list(lexer)

    def _get_class_mappings_entity(self, nodename):
        if not self._class_mappings:
            return Entity(name='empty (class mappings)')
        c = Classes()
        for mapping in self._class_mappings:
            matched = False
            key, klasses = Core._shlex_split(mapping)
            if key[0] == ('/'):
                matched = Core._match_regexp(key[1:-1], nodename)
                if matched:
                    for klass in klasses:
                        c.append_if_new(matched.expand(klass))

            else:
                if Core._match_glob(key, nodename):
                    for klass in klasses:
                        c.append_if_new(klass)

        return Entity(classes=c,
                      name='class mappings for node {0}'.format(nodename))

    def _get_input_data_entity(self):
        if not self._input_data:
            return Entity(name='empty (input data)')
        p = Parameters(self._input_data)
        return Entity(parameters=p, name='input data')

    def _recurse_entity(self, entity, merge_base=None, seen=None, nodename=None):
        if seen is None:
            seen = {}

        if merge_base is None:
            merge_base = Entity(name='empty (@{0})'.format(nodename))

        cnf_r = None # class_notfound_regexp compiled
        for klass in entity.classes.as_list():
            if klass not in seen:
                try:
                    class_entity = self._storage.get_class(klass)
                except ClassNotFound, e:
                    if self._ignore_class_notfound:
                        if not cnf_r:
                            cnf_r = re.compile('|'.join([x for x in self._ignore_class_regexp]))
                        if cnf_r.match(klass):
                            # TODO, add logging handler
                            print >>sys.stderr, "[WARNING] Reclass class not found: '%s'. Skipped!" % klass
                            continue
                    e.set_nodename(nodename)
                    raise e

                descent = self._recurse_entity(class_entity, seen=seen,
                                               nodename=nodename)
                # on every iteration, we merge the result of the recursive
                # descent into what we have so far…
                merge_base.merge(descent)
                seen[klass] = True

        # … and finally, we merge what we have at this level into the
        # result of the iteration, so that elements at the current level
        # overwrite stuff defined by parents
        merge_base.merge(entity)
        return merge_base

    def _get_automatic_parameters(self, nodename):
        if AUTOMATIC_RECLASS_PARAMETERS:
            return Parameters({ '_reclass_': { 'name': { 'full': nodename, 'short': string.split(nodename, '.')[0] } } })
        else:
            return Parameters()


    def _nodeinfo(self, nodename, exports):
        node_entity = self._storage.get_node(nodename)
        base_entity = Entity(name='base')
        base_entity.merge(self._get_class_mappings_entity(node_entity.name))
        base_entity.merge(self._get_input_data_entity())
        base_entity.merge_parameters(self._get_automatic_parameters(nodename))
        seen = {}
        merge_base = self._recurse_entity(base_entity, seen=seen,
                                          nodename=base_entity.name)
        ret = self._recurse_entity(node_entity, merge_base, seen=seen,
                                   nodename=node_entity.name)
        ret.interpolate(nodename, exports)
        return ret

    def _nodeinfo_as_dict(self, nodename, entity):
        ret = {'__reclass__' : {'node': entity.name, 'name': nodename,
                                'uri': entity.uri,
                                'environment': entity.environment,
                                'timestamp': Core._get_timestamp()
                               },
              }
        ret.update(entity.as_dict())
        return ret

    def _update_exports(self, old, new):
        old_yaml = yaml.dump(old.as_dict(), default_flow_style=True, Dumper=ExplicitDumper)
        new_yaml = yaml.dump(new.as_dict(), default_flow_style=True, Dumper=ExplicitDumper)
        if old_yaml != new_yaml:
            self._storage.put_exports(new)
            return True
        else:
            return False

    def nodeinfo(self, nodename):
        original_exports = Parameters(self._storage.get_exports())
        exports = copy.deepcopy(original_exports)
        original_exports.render_simple()
        ret = self._nodeinfo_as_dict(nodename, self._nodeinfo(nodename, exports))
        self._update_exports(original_exports, exports)
        return ret

    def inventory(self):
        original_exports = Parameters(self._storage.get_exports())
        exports = copy.deepcopy(original_exports)
        original_exports.render_simple()
        entities = {}
        for n in self._storage.enumerate_nodes():
            entities[n] = self._nodeinfo(n, exports)
        changed = self._update_exports(original_exports, exports)
        if changed:
            # use brute force: if the exports have changed rerun
            # the inventory cacluation
            #exports = Parameters(exports.as_dict())
            entities = {}
            for n in self._storage.enumerate_nodes():
                entities[n] = self._nodeinfo(n, exports)

        nodes = {}
        applications = {}
        classes = {}
        for f, nodeinfo in entities.iteritems():
            d = nodes[f] = self._nodeinfo_as_dict(f, nodeinfo)
            for a in d['applications']:
                if a in applications:
                    applications[a].append(f)
                else:
                    applications[a] = [f]
            for c in d['classes']:
                if c in classes:
                    classes[c].append(f)
                else:
                    classes[c] = [f]

        return {'__reclass__' : {'timestamp': Core._get_timestamp()},
                'nodes': nodes,
                'classes': classes,
                'applications': applications
               }
