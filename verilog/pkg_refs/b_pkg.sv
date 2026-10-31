// Package that depends on another package via a scoped reference (no import).
package b_pkg;
  localparam int N = $bits(a_pkg::word_t);
endpackage
