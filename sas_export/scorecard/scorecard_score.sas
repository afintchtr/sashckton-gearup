/* Behavioural risk scorecard (proposal 6.1). Points scale: base 600 at odds 50:1, PDO 40. Tier cuts in tier_cuts.json */
data work.scored; set work.input;
  risk_points = 0;
  /* payee_concentration_90 */
  if missing(payee_concentration_90) then pts_payee_concentration_90 = 128.33;
  else if payee_concentration_90 < 0.7217908054590225 then pts_payee_concentration_90 = 150.48;
  else if payee_concentration_90 < 0.8475645631551743 then pts_payee_concentration_90 = 146.72;
  else if payee_concentration_90 < 0.857856422662735 then pts_payee_concentration_90 = 136.67;
  else if payee_concentration_90 < 0.872175931930542 then pts_payee_concentration_90 = 134.35;
  else if payee_concentration_90 < 0.877727672457695 then pts_payee_concentration_90 = 131.08;
  else if payee_concentration_90 < 0.8828579187393188 then pts_payee_concentration_90 = 127.38;
  else if payee_concentration_90 < 0.8875916451215744 then pts_payee_concentration_90 = 126.84;
  else if payee_concentration_90 < 0.8968959152698517 then pts_payee_concentration_90 = 121.47;
  else if payee_concentration_90 < 0.9016181230545044 then pts_payee_concentration_90 = 117.85;
  else if payee_concentration_90 < 0.9178183525800705 then pts_payee_concentration_90 = 117.55;
  else pts_payee_concentration_90 = 116.79;
  risk_points + pts_payee_concentration_90;
  /* inflow_cv_90 */
  if missing(inflow_cv_90) then pts_inflow_cv_90 = 129.35;
  else if inflow_cv_90 < 0.03026661239564419 then pts_inflow_cv_90 = 120.18;
  else if inflow_cv_90 < 0.06961314529180528 then pts_inflow_cv_90 = 121.7;
  else if inflow_cv_90 < 0.11724224425852302 then pts_inflow_cv_90 = 122.14;
  else if inflow_cv_90 < 0.1441398799419403 then pts_inflow_cv_90 = 129.25;
  else if inflow_cv_90 < 0.7690614372491837 then pts_inflow_cv_90 = 132.6;
  else pts_inflow_cv_90 = 134.11;
  risk_points + pts_inflow_cv_90;
  /* passthrough_7 */
  if missing(passthrough_7) then pts_passthrough_7 = 128.33;
  else if passthrough_7 < 0.11803923547267914 then pts_passthrough_7 = 122.69;
  else if passthrough_7 < 0.21361786872148517 then pts_passthrough_7 = 126.71;
  else pts_passthrough_7 = 130.82;
  risk_points + pts_passthrough_7;
  /* declared_income */
  if missing(declared_income) then pts_declared_income = 128.33;
  else if declared_income < 2300000.0 then pts_declared_income = 159.76;
  else if declared_income < 3100000.0 then pts_declared_income = 152.38;
  else if declared_income < 3900000.0 then pts_declared_income = 144.1;
  else if declared_income < 5300000.0 then pts_declared_income = 139.18;
  else if declared_income < 6700000.0 then pts_declared_income = 126.33;
  else pts_declared_income = 122.7;
  risk_points + pts_declared_income;
  /* distinct_per_tx_30 */
  if missing(distinct_per_tx_30) then pts_distinct_per_tx_30 = 128.33;
  else if distinct_per_tx_30 < 0.6842105388641357 then pts_distinct_per_tx_30 = 132.46;
  else if distinct_per_tx_30 < 0.734375 then pts_distinct_per_tx_30 = 131.5;
  else if distinct_per_tx_30 < 0.7714285850524902 then pts_distinct_per_tx_30 = 130.08;
  else if distinct_per_tx_30 < 0.782608687877655 then pts_distinct_per_tx_30 = 128.1;
  else if distinct_per_tx_30 < 0.8235294222831726 then pts_distinct_per_tx_30 = 127.88;
  else if distinct_per_tx_30 < 0.8333333134651184 then pts_distinct_per_tx_30 = 126.84;
  else if distinct_per_tx_30 < 0.8947368264198303 then pts_distinct_per_tx_30 = 126.15;
  else pts_distinct_per_tx_30 = 124.71;
  risk_points + pts_distinct_per_tx_30;
run;
