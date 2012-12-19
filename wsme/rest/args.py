import cgi
import datetime
import re

from simplegeneric import generic

from wsme.exc import UnknownArgument

from wsme.types import iscomplex, list_attributes, Unset
from wsme.types import UserType, ArrayType, DictType, File
from wsme.utils import parse_isodate, parse_isotime, parse_isodatetime

ARRAY_MAX_SIZE = 1000


@generic
def from_param(datatype, value):
    return datatype(value) if value else None


@from_param.when_object(datetime.date)
def date_from_param(datatype, value):
    return parse_isodate(value) if value else None


@from_param.when_object(datetime.time)
def time_from_param(datatype, value):
    return parse_isotime(value) if value else None


@from_param.when_object(datetime.datetime)
def datetime_from_param(datatype, value):
    return parse_isodatetime(value) if value else None


@from_param.when_object(File)
def filetype_from_param(datatype, value):
    if isinstance(value, cgi.FieldStorage):
        return File(fieldstorage=value)
    return File(content=value)


@from_param.when_type(UserType)
def usertype_from_param(datatype, value):
    return datatype.frombasetype(
        from_param(datatype.basetype, value))


@from_param.when_type(ArrayType)
def array_from_param(datatype, value):
    if value is None:
        return value
    return [
        from_param(datatype.item_type, item)
        for item in value
    ]


@generic
def from_params(datatype, params, path, hit_paths):
    if iscomplex(datatype) and datatype is not File:
        objfound = False
        for key in params:
            if key.startswith(path + '.'):
                objfound = True
                break
        if objfound:
            r = datatype()
            for attrdef in list_attributes(datatype):
                value = from_params(attrdef.datatype,
                        params, '%s.%s' % (path, attrdef.key), hit_paths)
                if value is not Unset:
                    setattr(r, attrdef.key, value)
            return r
    else:
        if path in params:
            hit_paths.add(path)
            return from_param(datatype, params[path])
    return Unset


@from_params.when_type(ArrayType)
def array_from_params(datatype, params, path, hit_paths):
    if path in params:
        hit_paths.add(path)
        return [
            from_param(datatype.item_type, value)
            for value in params.getall(path)]

    if iscomplex(datatype.item_type):
        attributes = set()
        r = re.compile('^%s\.(?P<attrname>[^\.])' % re.escape(path))
        for p in params.keys():
            m = r.match(p)
            if m:
                attributes.add(m.group('attrname'))
        if attributes:
            value = []
            for attrdef in list_attributes(datatype.item_type):
                attrpath = '%s.%s' % (path, attrdef.key)
                hit_paths.add(attrpath)
                attrvalues = params.getall(attrpath)
                if len(value) < len(attrvalues):
                    value[-1:] = [
                        datatype.item_type()
                        for i in xrange(len(attrvalues) - len(value))
                    ]
                for i, attrvalue in enumerate(attrvalues):
                    setattr(
                        value[i],
                        attrdef.key,
                        from_param(attrdef.datatype, attrvalue)
                    )
            return value

    indexes = set()
    r = re.compile('^%s\[(?P<index>\d+)\]' % re.escape(path))

    for p in params.keys():
        m = r.match(p)
        if m:
            indexes.add(int(m.group('index')))

    if not indexes:
        return Unset

    indexes = list(indexes)
    indexes.sort()

    return [from_params(datatype.item_type, params,
                        '%s[%s]' % (path, index), hit_paths)
            for index in indexes]


@from_params.when_type(DictType)
def dict_from_params(datatype, params, path, hit_paths):

    keys = set()
    r = re.compile('^%s\[(?P<key>[a-zA-Z0-9_\.]+)\]' % re.escape(path))

    for p in params.keys():
        m = r.match(p)
        if m:
            keys.add(from_param(datatype.key_type, m.group('key')))

    if not keys:
        return Unset

    return dict((
        (key, from_params(datatype.value_type,
                          params, '%s[%s]' % (path, key), hit_paths))
        for key in keys))


def args_from_args(funcdef, args, kwargs):
    newargs = []
    for argdef, arg in zip(funcdef.arguments[:len(args)], args):
        newargs.append(from_param(argdef.datatype, arg))
    newkwargs = {}
    for argname, value in kwargs.items():
        newkwargs[argname] = from_param(funcdef.get_arg(argname), value)
    return newargs, newkwargs


def args_from_params(funcdef, params):
    kw = {}
    hit_paths = set()
    for argdef in funcdef.arguments:
        value = from_params(
            argdef.datatype, params, argdef.name, hit_paths)
        if value is not Unset:
            kw[argdef.name] = value
    paths = set(params.keys())
    unknown_paths = paths - hit_paths
    if unknown_paths:
        raise UnknownArgument(', '.join(unknown_paths))
    return [], kw


def args_from_body(funcdef, body, mimetype):
    from wsme.rest import json as restjson
    from wsme.rest import xml as restxml

    if funcdef.body_type is not None:
        datatypes = {funcdef.arguments[-1].name: funcdef.body_type}
    else:
        datatypes = dict(((a.name, a.datatype) for a in funcdef.arguments))

    if not body:
        return (), {}
    if mimetype in restjson.accept_content_types:
        dataformat = restjson
    elif mimetype in restxml.accept_content_types:
        dataformat = restxml
    else:
        raise ValueError("Unknow mimetype: %s" % mimetype)

    kw = dataformat.parse(
        body, datatypes, bodyarg=funcdef.body_type is not None
    )

    return (), kw


def combine_args(funcdef, *akw):
    newargs, newkwargs = [], {}
    for args, kwargs in akw:
        for i, arg in enumerate(args):
            newkwargs[funcdef.arguments[i].name] = arg
        for name, value in kwargs.items():
            newkwargs[str(name)] = value
    return newargs, newkwargs


def get_args(funcdef, args, kwargs, params, body, mimetype):
    return combine_args(
        funcdef,
        args_from_args(funcdef, args, kwargs),
        args_from_params(funcdef, params),
        args_from_body(funcdef, body, mimetype),
    )