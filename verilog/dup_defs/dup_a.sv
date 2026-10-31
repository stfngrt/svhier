// First copy of dup_mod (shadowed by dup_b.sv).
module dup_leaf_a; endmodule
module dup_mod; dup_leaf_a u_a(); endmodule
