'use strict'

// The one place that asks the host for React. Everything else takes `h` and the hooks from here, so a
// test or a later change has a single module to replace. No JSX: elements are built with createElement.

const React = require('react')

const h = React.createElement
const { useEffect, useRef, useState } = React

module.exports = { React, h, useEffect, useRef, useState }
