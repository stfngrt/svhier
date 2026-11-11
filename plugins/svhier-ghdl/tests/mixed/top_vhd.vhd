library ieee; use ieee.std_logic_1164.all; use work.my_pkg.all;
entity top_vhd is generic (N : integer := 2); end entity;
architecture rtl of top_vhd is
  component leaf_vhd is generic (W : integer := WIDTH); end component;
  component sv_child is port (clk : in std_logic); end component;
  component sv_req is generic (DEPTH : integer); end component;
  component nowhere is end component;
  signal clk : std_logic;
begin
  u_sv : SV_CHILD port map (clk => clk);
  u_req : sv_req generic map (DEPTH => N * 2);
  u_none : nowhere;
  g_for : for i in 0 to N-1 generate
    u_g : leaf_vhd;
  end generate;
  g_if : if N > 5 generate
    u_off : leaf_vhd;
  end generate;
  blk : block begin
    u_b : entity work.leaf_vhd(sim);
  end block;
end architecture;
