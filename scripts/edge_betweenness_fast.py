#!/usr/bin/env python3
"""Edge betweenness with igraph, for build_street_measures.py.

networkx takes many minutes on graphs above about 8,000 corners. igraph runs the same
Brandes algorithm in C. Run with a Python that has igraph (cache/venv-overture).
Input JSON: {"n": node count, "edges": [[u, v, length], ...]}. Output JSON: one value per
edge, in input order, each unordered pair of corners counted once (the networkx
normalized=False convention for undirected graphs).
"""
import json
import sys

import igraph


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = json.load(open(src, encoding="utf-8"))
    graph = igraph.Graph(n=data["n"], edges=[(u, v) for u, v, _ in data["edges"]], directed=False)
    values = graph.edge_betweenness(directed=False, weights=[length for _, _, length in data["edges"]])
    json.dump(values, open(dst, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
