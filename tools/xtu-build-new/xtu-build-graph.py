#!/usr/bin/python
# -*- coding: utf-8 -*-

import copy
import re
import os
import stat
import argparse
import itertools
from collections import namedtuple
from collections import defaultdict
import json

parser = argparse.ArgumentParser(description='generate build dependency graph')
parser.add_argument('-b', dest='commands_file',
                    help='absolute path to compile_commands.json (including file name)')
parser.add_argument('-c', dest='cfg_file', help='path to cfg.txt')
parser.add_argument('-o', dest='out_file', default='build_graph.json',
                    help='output file')

args = parser.parse_args()
if not args.commands_file:
    parser.error('compile_commands.json is required')

InOut = namedtuple("InOut", "into out")


def remove_nodes(g, nodes):
    for node in nodes:
        for pointed in g[node].out:
            g[pointed].into.remove(node)
        g.pop(node)
    nodes = []


def eliminate_circles(g):
    removable_edges = dict()
    for node in g:
        removable_edges[node] = InOut(set(), set())

    while g:
        no_dependency = [node for node in g if len(g[node].into) == 0]
        if len(no_dependency) > 0:
            remove_nodes(g, no_dependency)
            continue
        removable_node = max(iter(list(g.keys())),
                       key=(lambda key: len(g[key].out)))
        for node in g[removable_node].into:
            g[node].out.remove(removable_node)
            removable_edges[node].out.add(removable_node)
            removable_edges[removable_node].into.add(node)
        for node in g[removable_node].out:
            g[node].into.remove(removable_node)
        g.pop(removable_node)
    return removable_edges


def topological_order(graph):  # works only on DAG
    topological_order = []
    top_seed = [node for node in graph if len(graph[node].into) == 0]
    while top_seed:
        node = top_seed.pop()
        topological_order.append(node)
        for m in graph[node].out:
            graph[m].into.remove(node)
            if len(graph[m].into) == 0:
                top_seed.append(m)
        graph.pop(node)
    return topological_order


def main():
        #-------------- obtain function-to-file mapping --------------#
    print('Obtaining function-to-file mapping')
    #sys.stdout.flush()

    tmpdir = ".xtu/"

    fns = dict()
    external_map = dict()

    defined_fns_filename = tmpdir + "definedFns.txt"
    os.chmod(defined_fns_filename, stat.S_IRUSR)
    with open(defined_fns_filename, "r") as defined_fns_file:
        for line in defined_fns_file:
            funcname, filename = line.strip().split(' ')
            if funcname.startswith('!'):
                funcname = funcname[1:]
            fns[funcname] = filename

    extern_fns_filename = tmpdir + "externalFns.txt"
    os.chmod(extern_fns_filename, stat.S_IRUSR)
    with open(extern_fns_filename, "r") as extern_fns_file:
        for line in extern_fns_file:
            line = line.strip()
            if line in fns and not line in external_map:
                external_map[line] = fns[line]

    with open(tmpdir + "externalFnMap.txt", "w") as out_file:
        for func, fname in list(external_map.items()):
            out_file.write("%s %s.ast\n" % (func, fname))

    #-------------- analyze call graph to find analysis order --------------#

    cfg = dict()
    func_set = set()

    print('Obtaining analysis order')
    #sys.stdout.flush()

    callees_glob = set()
    ast_regexp = re.compile("^/ast/(?:\w)+")

    # Read call graph
    #if(args.cfg_file):
    #    cfg_filename = args.cfg_file
    #else:
    cfg_filename = tmpdir + "cfg.txt"

    os.chmod(cfg_filename, stat.S_IRUSR)
    with open(cfg_filename, "r") as cfg_file:
        for line in cfg_file:
            funcs = line.strip().split(' ')
            key = funcs[0]
            arch = key.split("@")[-1]
            key = re.sub("@" + arch, "", key)
            func_set.add(key)
            filename, func = key.split("::")
            filename = filename.split("@")[0]
            callees = set()
            for callee in funcs[1:]:
                if callee.startswith("::"):
                    fname = filename + callee.split("@")[0]
                    callees.add(fname)
                    func_set.add(fname)
                elif callee in external_map:
                    arch = callee.split("@")[-1]
                    fname = re.sub(ast_regexp, "", external_map[callee]) + \
                                "::" + callee.split("@")[0]
                    callees.add(fname)
                    func_set.add(fname)
            if callees:
                cfg[key] = callees
                callees_glob |= callees

    # Read compile_commands.json

    src_pattern = re.compile(".*\.(C|c|cc|cpp|cxx|ii|m|mm)$")
    with open(args.commands_file, "r") as build_args_file:
        build_json = json.load(build_args_file)

    commandlist = [command for command in build_json
                if src_pattern.match(command['file'])]

    compile_commands_id = {commandlist[i]['command']: i for i in range(0, len(commandlist))}
    command_id_to_compile_command_id = []

    sorted_commands = sorted(commandlist)
    file_to_command_ids = defaultdict(set)
    command_id = 0
    for buildcommand in sorted_commands:
        command_id_to_compile_command_id.append(compile_commands_id[buildcommand['command']])
        file_to_command_ids[buildcommand['file']].add(command_id)
        command_id += 1

    print("build build_graph")
    # Create build_commands dependency graph based on function calls
    # (and containing files)

    build_graph = defaultdict(InOut)
    for fid in range(0, command_id):
        build_graph[fid] = InOut(set(), set())

    for caller, callees in list(cfg.items()):
        callerfile = caller.split('::')[0]
        for callerbuild_id in file_to_command_ids[callerfile]:
            for callee in callees:
                for calleebuild_id in file_to_command_ids[callee.split('::')[0]]:
                    if(calleebuild_id != callerbuild_id):
                        build_graph[callerbuild_id].out.add(calleebuild_id)
                        build_graph[calleebuild_id].into.add(callerbuild_id)

    #print build_graph

    # eliminate circles from build_graph
    build_graph_copy = copy.deepcopy(build_graph)
    print("eliminate circles")
    removable_edges = eliminate_circles(build_graph_copy)

    build_graph = {
    key: InOut(
    build_graph[key].into - removable_edges.get(key, InOut(set(), set())).into,
    build_graph[key].out - removable_edges.get(key, InOut(set(), set())).out)
    for key in list(build_graph.keys())
    }
    print("write build_dependency.json")
    with open(tmpdir + "build_dependency.json", "w") as dependency_file:
        list_graph = []
        for n in build_graph:
            for m in build_graph[n].out:
                    list_graph.append((command_id_to_compile_command_id[n],
                                       command_id_to_compile_command_id[m]))
        dependency_file.write(json.dumps(list_graph))

    # topological order of build_graph
    file_order = topological_order(build_graph)
    print("write topological order to order.txt")
    with open(tmpdir + "order.txt", "w") as order_file:
        for file_id in file_order:
            order_file.write(sorted_commands[file_id]['command'])
            order_file.write("\n")


if __name__ == "__main__":
    main()
