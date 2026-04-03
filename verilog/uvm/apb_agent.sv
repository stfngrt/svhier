// APB UVM agent: driver, monitor, and agent wrapper modules
module apb_driver
  import uvm_pkg::*;
  (apb_if.master_mp apb);
endmodule

module apb_monitor
  import uvm_pkg::*;
  (apb_if.slave_mp apb);
endmodule

// Agent instantiates driver and monitor
module apb_agent
  import uvm_pkg::*;
  (apb_if apb);
  apb_driver  u_drv (.apb(apb));
  apb_monitor u_mon (.apb(apb));
endmodule
