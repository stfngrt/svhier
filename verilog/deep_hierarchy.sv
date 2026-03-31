// Three-level hierarchy: Top -> Mid -> Leaf
module Leaf;
endmodule

module Mid;
  Leaf u_leaf ();
endmodule

// Top instantiates Mid twice
module Top;
  Mid u_mid0 ();
  Mid u_mid1 ();
endmodule
