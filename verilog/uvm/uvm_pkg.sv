// Minimal self-contained UVM stub — defines the base classes used by the agent files
package uvm_pkg;
  virtual class uvm_object;
  endclass

  virtual class uvm_component extends uvm_object;
  endclass

  virtual class uvm_driver #(type REQ = uvm_object) extends uvm_component;
  endclass

  virtual class uvm_monitor extends uvm_component;
  endclass

  virtual class uvm_agent extends uvm_component;
  endclass

  virtual class uvm_env extends uvm_component;
  endclass

  virtual class uvm_test extends uvm_component;
  endclass
endpackage
