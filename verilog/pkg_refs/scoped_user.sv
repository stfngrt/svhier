// Uses b_pkg::N without importing b_pkg; takes bus_if as a bare interface port.
module scoped_user (bus_if bus);
  logic [b_pkg::N-1:0] data;
endmodule
