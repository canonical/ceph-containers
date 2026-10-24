*** Settings ***
Documentation       CephX credential rotation on a disposable single-host cephadm cluster.
...                 Uses ceph auth rotate, not orch daemon rotate-keys.
...                 Tests rotation with the preferred cipher, not cipher migration.
Library             CephKeyRotation.py
Suite Setup         Deploy And Prepare Rotation Suite
Suite Teardown      Finish Rotation Suite    ${SUITE STATUS}

*** Variables ***
${CEPH_IMAGE}        localhost:5000/canonical/ceph:latest

*** Test Cases ***
Rotate The Shared Monitor Key
    Given A Healthy Cephadm Cluster
    When All Service Keys Are Rotated    mon
    Then Cluster Should Be Healthy
    And Data Should Survive Rotation
    And CephFS Metadata Should Be Accessible

Rotate Every Manager Key
    Given A Healthy Cephadm Cluster
    When All Service Keys Are Rotated    mgr
    Then Cluster Should Be Healthy
    And Data Should Survive Rotation
    And CephFS Metadata Should Be Accessible

Rotate Every OSD Key
    Given A Healthy Cephadm Cluster
    When All Service Keys Are Rotated    osd
    Then Cluster Should Be Healthy
    And Data Should Survive Rotation
    And CephFS Metadata Should Be Accessible

Rotate Every MDS Key
    Given A Healthy Cephadm Cluster
    When All Service Keys Are Rotated    mds
    Then Cluster Should Be Healthy
    And Data Should Survive Rotation
    And CephFS Metadata Should Be Accessible

Rotate A Client Key
    Given A Healthy Cephadm Cluster
    When The Client Key Is Rotated And Imported
    Then The New Client Key Should Allow Data Access
    And The Old Client Key Should Be Rejected
    And The New Client Key Should Allow Data Access
    And Cluster Should Be Healthy

*** Keywords ***
Deploy And Prepare Rotation Suite
    Deploy Single Node Cluster    ${CEPH_IMAGE}
    Prepare Rotation Suite

A Healthy Cephadm Cluster
    Cluster Should Be Healthy
