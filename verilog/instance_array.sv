// Instance arrays (1-D and 2-D).
module arr_leaf; endmodule

module arr_top;
  arr_leaf u_arr [1:0] ();
  arr_leaf u_2d [1:0][1:0] ();
endmodule
