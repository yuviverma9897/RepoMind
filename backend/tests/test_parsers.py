"""Structure detection per language. Every case also checks exact line numbers via helpers.chunk."""

from tests.helpers import chunk, symbols


def span(chunks, name):
    """(first line, last line) of a symbol, even if it was split into parts."""
    parts = [c for c in chunks if c.symbol_name == name]
    return min(c.start_line for c in parts), max(c.end_line for c in parts)


def kinds(chunks):
    return [(c.chunk_type, c.symbol_name, c.parent_symbol) for c in chunks]


def test_javascript_functions_classes_imports_and_tricky_literals():
    source = """import React, { useState } from 'react';
import {
  a,
  b,
} from "./x";

// Adds numbers.
export function add(a, b) {
  const s = "}{";          // braces in strings
  const t = `${a} } ${b}`; // and in template literals
  const r = /[{]/;         // and in regex literals
  return a + b;
}

export const Button = ({ label }) => {
  return <button>{label}</button>;
};

const square = (x) => x * x;

class Store extends Base {
  #items = [];
  constructor() {
    super();
  }
  async load(id) {
    return fetch(id);
  }
  handle = (e) => {
    console.log(e);
  };
}

export default function App() {
  return null;
}
"""
    _, chunks = chunk(source, "jsx")
    assert symbols(chunks) == [
        ("imports", None, 1, 5),
        ("function", "add", 7, 13),
        ("function", "Button", 15, 17),
        ("function", "square", 19, 19),
        ("class", "Store", 21, 32),
        ("function", "App", 34, 36),
    ]


def test_javascript_large_class_splits_into_methods():
    methods = "\n".join(f"  method{i}(arg) {{\n    return compute(arg, {i}); // some padding here\n  }}" for i in range(30))
    source = f"export class Big {{\n  field = 1;\n{methods}\n}}\n"
    _, chunks = chunk(source, "javascript", size=400, overlap=40)
    methods_found = [c for c in chunks if c.chunk_type == "method"]
    assert len(methods_found) == 30
    assert all(c.parent_symbol == "Big" for c in methods_found)
    assert chunks[0].chunk_type == "class" and chunks[0].start_line == 1


def test_typescript_interface_type_enum():
    source = """export interface User {
  id: number;
  save(): Promise<void>;
}

export type Point = {
  x: number;
};

export type Id = string | number;

export enum Color {
  Red,
  Green,
}

export async function load<T>(id: Id): Promise<T> {
  return get(id);
}
"""
    _, chunks = chunk(source, "typescript")
    assert symbols(chunks) == [
        ("interface", "User", 1, 4),
        ("type", "Point", 6, 8),
        ("module", None, 10, 10),
        ("enum", "Color", 12, 15),
        ("function", "load", 17, 19),
    ]


def test_java_class_methods_annotations_constructor():
    source = """package com.example;

import java.util.List;

/** A service. */
@Service
public class UserService {
    private final Repo repo;

    public UserService(Repo repo) {
        this.repo = repo;
    }

    @Override
    public List<User> findAll(int page,
                              int size) throws IOException {
        if (page > 0) {
            return repo.find(page);
        }
        return List.of();
    }

    abstract void notImplemented();
}
"""
    _, chunks = chunk(source, "java", size=200, overlap=20)
    found = kinds(chunks)
    assert ("imports", None, None) == found[0] and chunks[0].end_line == 3
    assert ("method", "UserService", "UserService") in found
    assert ("method", "findAll", "UserService") in found
    assert span(chunks, "findAll") == (14, 21)  # includes @Override
    assert not any(c.symbol_name == "notImplemented" and c.chunk_type == "method" for c in chunks)


def test_go_functions_methods_structs_imports():
    source = """package server

import (
    "fmt"
    "net/http"
)

// Server serves.
type Server struct {
    addr string
}

func New(addr string) *Server {
    return &Server{addr: addr}
}

func (s *Server) Start() error {
    fmt.Println("{")
    return nil
}
"""
    _, chunks = chunk(source, "go")
    assert symbols(chunks) == [
        ("imports", None, 1, 6),
        ("struct", "Server", 8, 11),
        ("function", "New", 13, 15),
        ("method", "Start", 17, 20),
    ]
    assert chunks[-1].parent_symbol == "Server"


def test_rust_fn_impl_trait_and_lifetimes():
    source = """use std::fmt;

/// A point.
#[derive(Debug)]
pub struct Point<'a> {
    name: &'a str,
}

impl<'a> fmt::Display for Point<'a> {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}", self.name)
    }
}

pub fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() { x } else { y }
}
"""
    _, chunks = chunk(source, "rust", size=120, overlap=10)
    found = kinds(chunks)
    assert found[0] == ("imports", None, None)
    point = next(c for c in chunks if c.symbol_name == "Point")
    assert (point.chunk_type, point.start_line, point.end_line) == ("struct", 3, 7)
    assert ("method", "fmt", "Point<'a>") in found
    longest = next(c for c in chunks if c.symbol_name == "longest")
    assert (longest.start_line, longest.end_line) == (15, 17)


def test_c_and_cpp_functions_structs_classes_namespaces():
    c_source = """#include <stdio.h>
#include "util.h"

int add(int a, int b);

struct point {
    int x, y;
};

static int *make(int n)
{
    return malloc(n);
}
"""
    _, chunks = chunk(c_source, "c")
    assert symbols(chunks) == [
        ("imports", None, 1, 2),
        ("module", None, 4, 4),  # a prototype is not a definition
        ("struct", "point", 6, 8),
        ("function", "make", 10, 13),
    ]

    cpp_source = """namespace geo {

class Shape {
public:
    virtual double area() const = 0;
    double scale(double f) {
        return area() * f;
    }
};

double Shape::perimeter() const {
    return 0;
}

}
"""
    _, chunks = chunk(cpp_source, "cpp", size=60, overlap=0)
    found = kinds(chunks)
    assert ("method", "scale", "Shape") in found
    assert ("function", "Shape::perimeter", "geo") in found


def test_csharp_namespace_class_methods_allman_braces():
    source = """using System;

namespace App
{
    [Serializable]
    public class Greeter
    {
        public string Greet(string name)
        {
            return $"Hello {name}";
        }
    }
}
"""
    _, chunks = chunk(source, "csharp", size=80, overlap=0)
    found = kinds(chunks)
    assert found[0] == ("imports", None, None)
    assert ("method", "Greet", "Greeter") in found
    assert span(chunks, "Greet") == (8, 11)


def test_markdown_sections_ignore_code_fences():
    source = """Intro text.

# Title

Some text.

```bash
# not a heading
```

## Install

Run it.
"""
    _, chunks = chunk(source, "markdown")
    assert symbols(chunks) == [
        ("section", None, 1, 1),
        ("section", "Title", 3, 9),
        ("section", "Install", 11, 13),
    ]


def test_unbalanced_braces_do_not_crash():
    source = "function broken() {\n  if (x) {\n    return 1;\n\nconst y = 2;\n"
    result, chunks = chunk(source, "javascript")
    assert result.parser == "brace-heuristic"
    assert chunks  # falls back to top-level code chunks


def test_extern_c_block_is_a_container():
    source = 'extern "C" {\n' + "".join(f"int f{i}(void) {{\n    return {i};\n}}\n" for i in range(20)) + "}\n"
    _, chunks = chunk(source, "c", size=100, overlap=0)
    assert [c.symbol_name for c in chunks if c.chunk_type == "function"] == [f"f{i}" for i in range(20)]
