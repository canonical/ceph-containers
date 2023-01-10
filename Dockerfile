# CEPH DAEMON BASE IMAGE

FROM ubuntu:jammy

ENV I_AM_IN_A_CONTAINER 1

# Who is the maintainer ?
LABEL maintainer=""

# Is a ceph container ?
LABEL ceph="True"

# What is the actual release ? If not defined, this equals the git branch name
LABEL RELEASE="main"

# What was the url of the git repository
LABEL GIT_REPO="https://github.com/UtkarshBhatthere/ceph-container.git"

# What was the git branch used to build this container
LABEL GIT_BRANCH="main"

# What was the commit ID of the current HEAD
LABEL GIT_COMMIT="f77ca5de7910f1e3de260a1218c757954afd8327"

# Was the repository clean when building ?
LABEL GIT_CLEAN="False"

# What CEPH_POINT_RELEASE has been used ?
LABEL CEPH_POINT_RELEASE=""

ENV CEPH_VERSION pacific
ENV CEPH_POINT_RELEASE ""
ENV CEPH_DEVEL false
ENV CEPH_REF pacific
ENV OSD_FLAVOR default

#======================================================
# Install ceph and dependencies, and clean up
#======================================================

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
DEBIAN_FRONTEND=noninteractive apt-get install -yy --force-yes --no-install-recommends \
gnupg curl apt-transport-https ca-certificates

# Escape char after immediately after RUN allows comment in first line
RUN \
    # Install all components for the image, whether from packages or web downloads.
    # Typical workflow: add new repos; refresh repos; install packages; package-manager clean;
    #   download and install packages from web, cleaning any files as you go.
    # Installs should support install of ganesha for luminous
    # add the necessary repos
    echo "" > /etc/apt/sources.list && \
    echo "deb http://archive.ubuntu.com/ubuntu/ jammy-backports main" \
      >> /etc/apt/sources.list.d/erp.list && \
    echo "deb http://archive.ubuntu.com/ubuntu/ jammy main universe multiverse" \
      >> /etc/apt/sources.list.d/jammy.list && \
    echo "deb http://archive.ubuntu.com/ubuntu/ jammy-updates main universe multiverse" \
      >> /etc/apt/sources.list.d/jammy.list && \
    DEBIAN_FRONTEND=noninteractive apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -yy --force-yes --no-install-recommends \
       \
        # No libstoragemgmt package on ubuntu, hence the override
        ca-certificates \
        e2fsprogs \
        ceph-common  \
        ceph-mon  \
        ceph-osd \
        ceph-mds \
        rbd-mirror  \
        ceph-mgr \
ceph-mgr-cephadm \
ceph-mgr-dashboard \
ceph-mgr-diskprediction-local \
ceph-mgr-k8sevents \
ceph-mgr-rook\
        ceph-grafana-dashboards \
        kmod \
        lvm2 \
        gdisk \
	smartmontools \
	nvme-cli \
        radosgw \
        nfs-ganesha nfs-ganesha-ceph \
        ceph-iscsi targetcli-fb \
        attr \
ceph-fuse \
rbd-nbd \
         && \
    # Clean container, starting with record of current size (strip / from end)
    INITIAL_SIZE="$(bash -c 'sz="$(du -sm --exclude=/proc /)" ; echo "${sz%*/}"')" && \
    #
    #
    # Perform any final cleanup actions like package manager cleaning, etc.
    echo 'Postinstall cleanup' && \
     ( echo "apt clean" && DEBIAN_FRONTEND=noninteractive apt-get clean && \
      echo "apt autoclean" && DEBIAN_FRONTEND=noninteractive apt-get autoclean ) || \
      ( retval=$? && cat /var/log/apt/history.log && exit $retval ) && \
    echo 'remove unneeded apt, deb, dpkg data' && \
      rm -rf /var/lib/apt/lists/* \
             /var/cache/debconf/* \
             /var/log/apt/ \
             /var/log/dpkg.log \ 
             /tmp/* && \
    /bin/true && \
    # Tweak some configuration files on the container system
    # disable sync with udev since the container can not contact udev
sed -i -e 's/udev_rules = 1/udev_rules = 0/' -e 's/udev_sync = 1/udev_sync = 0/' -e 's/obtain_device_list_from_udev = 1/obtain_device_list_from_udev = 0/' /etc/lvm/lvm.conf && \
# validate the sed command worked as expected
grep -sqo "udev_sync = 0" /etc/lvm/lvm.conf && \
grep -sqo "udev_rules = 0" /etc/lvm/lvm.conf && \
grep -sqo "obtain_device_list_from_udev = 0" /etc/lvm/lvm.conf && \
mkdir -p /var/run/ceph /var/run/ganesha && \
    # Clean common files like /tmp, /var/lib, etc.
    rm -rf \
        /etc/{selinux,systemd,udev} \
        /lib/{lsb,udev} \
        /tmp/* \
        /usr/lib{,64}/{locale,systemd,udev,dracut} \
        /usr/share/{doc,info,locale,man} \
        /usr/share/{bash-completion,pkgconfig/bash-completion.pc} \
        /var/log/* \
        /var/tmp/* && \
    find  / -xdev -name "*.pyc" -o -name "*.pyo" -exec rm -f {} \; && \
    # ceph-dencoder is only used for debugging, compressing it saves 10MB
    # If needed it will be decompressed
    # TODO: Is ceph-dencoder safe to remove as rook was trying to do?
    # rm -f /usr/bin/ceph-dencoder && \
    if [ -f /usr/bin/ceph-dencoder ]; then gzip -9 /usr/bin/ceph-dencoder; fi && \
    # TODO: What other ceph stuff needs removed/stripped/zipped here?
    # Photoshop files inside a container ?
    rm -f /usr/lib/ceph/mgr/dashboard/static/AdminLTE-*/plugins/datatables/extensions/TableTools/images/psd/* && \
    # Some logfiles are not empty, there is no need to keep them
    find /var/log/ -type f -exec truncate -s 0 {} \; && \
    #
    #
    # Report size savings (strip / from end)
    FINAL_SIZE="$(bash -c 'sz="$(du -sm --exclude=/proc /)" ; echo "${sz%*/}"')" && \
    REMOVED_SIZE=$((INITIAL_SIZE - FINAL_SIZE)) && \
    echo "Cleaning process removed ${REMOVED_SIZE}MB" && \
    echo "Dropped container size from ${INITIAL_SIZE}MB to ${FINAL_SIZE}MB" && \
    #
    # Verify that the packages installed haven't been accidentally cleaned
    apt-cache show \
        # No libstoragemgmt package on ubuntu, hence the override
        ca-certificates \
        e2fsprogs \
        ceph-common  \
        ceph-mon  \
        ceph-osd \
        ceph-mds \
        rbd-mirror  \
        ceph-mgr \
ceph-mgr-cephadm \
ceph-mgr-dashboard \
ceph-mgr-diskprediction-local \
ceph-mgr-k8sevents \
ceph-mgr-rook\
        ceph-grafana-dashboards \
        kmod \
        lvm2 \
        gdisk \
	smartmontools \
	nvme-cli \
        radosgw \
        nfs-ganesha nfs-ganesha-ceph \
        ceph-iscsi targetcli-fb \
        attr \
ceph-fuse \
rbd-nbd \
         && echo 'Packages verified successfully'

#======================================================
# Install ceph and dependencies, and clean up
#======================================================


# Escape char after immediately after RUN allows comment in first line
RUN \
    # Install all components for the image, whether from packages or web downloads.
    # Typical workflow: add new repos; refresh repos; install packages; package-manager clean;
    #   download and install packages from web, cleaning any files as you go.
    echo 'Install packages' && \
      DEBIAN_FRONTEND=noninteractive apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y \
      # python3-asyncssh and python-natsort are additional jammy dependencies.
        wget unzip uuid-runtime python-setuptools udev dmsetup ceph-volume python3-asyncssh python3-natsort && \
      apt-get install -y  --no-install-recommends --force-yes \
          sharutils \
          lsof \
           \
           \
          etcd-client \
          s3cmd && \
      apt-get clean && \
    # ubuntu does not have confd/kubectl packages, so install from web
    echo 'Web install confd' && \
      CONFD_VERSION=0.16.0 && \
      # Assume linux
      CONFD_ARCH=linux-amd64 && \
      wget -q -O /usr/local/bin/confd \
        "https://github.com/kelseyhightower/confd/releases/download/v${CONFD_VERSION}/confd-${CONFD_VERSION}-${CONFD_ARCH}" && \
      chmod +x /usr/local/bin/confd && mkdir -p /etc/confd/conf.d && mkdir -p /etc/confd/templates && \
    echo 'Web install kubectl' && \
      KUBECTL_VERSION=v1.8.11 && \
      # Assume linux
      KUBECTL_ARCH=amd64 && \
      wget -q -O /usr/local/bin/kubectl \
        "https://storage.googleapis.com/kubernetes-release/release/${KUBECTL_VERSION}/bin/linux/${KUBECTL_ARCH}/kubectl" && \
      chmod +x /usr/local/bin/kubectl && \
    # Clean container, starting with record of current size (strip / from end)
    INITIAL_SIZE="$(bash -c 'sz="$(du -sm --exclude=/proc /)" ; echo "${sz%*/}"')" && \
    #
    #
    # Perform any final cleanup actions like package manager cleaning, etc.
    echo 'Postinstall cleanup' && \
     ( echo "apt clean" && DEBIAN_FRONTEND=noninteractive apt-get clean && \
      echo "apt autoclean" && DEBIAN_FRONTEND=noninteractive apt-get autoclean ) || \
      ( retval=$? && cat /var/log/apt/history.log && exit $retval ) && \
    echo 'remove unneeded apt, deb, dpkg data' && \
      rm -rf /var/lib/apt/lists/* \
             /var/cache/debconf/* \
             /var/log/apt/ \
             /var/log/dpkg.log \ 
             /tmp/* && \
    echo "purge unneeded packages" && \
    DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove perl wget && \
    # NOTE: apt will fail with "E: Unmet dependencies." if `dpkg --purge --force-all` commands
    #       exist while it is trying to manage packages, so save aggressive removes for after.
    # Removing agressively perl-base as nothing we use call perl yet.
    # perl-base is required by adduser, init-system-helpers and debconf
    # At this stage of the build process, it's okay to break those tools for saving storage space
    echo "purge perl-base" && dpkg --purge --force-all perl-base libperl5.22 && \
    # Timezone is not configured so let's remove the zoneinfo (8MB)
    # BUG: dpkg --purge --force-all tzdata is returning an error. Disable for now.
    #      subprocess installed post-removal script returned error exit status 127
    # echo 'purge time zone info' && dpkg --purge --force-all tzdata && \
    echo 'remove unneeded apt, deb, dpkg data' && \
      rm -rf /var/lib/apt/lists/* \
             /var/cache/debconf/* \
             /var/log/apt/ \
             /var/log/dpkg.log && \
    # Clean daemon-specific files
    # Let's remove easy stuff
    rm -f /usr/bin/{etcd-tester,etcd-dump-logs} && \
    # Remove etcd since all we need is etcdctl
    rm -f /usr/bin/etcd && \
    # Uncomment below line for more detailed debug info
    # find / -xdev -type f -exec du -c {} \; |sort -n && \
    echo "CLEAN DAEMON DONE!" && \
    # Clean common files like /tmp, /var/lib, etc.
    rm -rf \
        /etc/{selinux,systemd,udev} \
        /lib/{lsb,udev} \
        /tmp/* \
        /usr/lib{,64}/{locale,systemd,udev,dracut} \
        /usr/share/{doc,info,locale,man} \
        /usr/share/{bash-completion,pkgconfig/bash-completion.pc} \
        /var/log/* \
        /var/tmp/* && \
    find  / -xdev -name "*.pyc" -o -name "*.pyo" -exec rm -f {} \; && \
    # ceph-dencoder is only used for debugging, compressing it saves 10MB
    # If needed it will be decompressed
    # TODO: Is ceph-dencoder safe to remove as rook was trying to do?
    # rm -f /usr/bin/ceph-dencoder && \
    if [ -f /usr/bin/ceph-dencoder ]; then gzip -9 /usr/bin/ceph-dencoder; fi && \
    # TODO: What other ceph stuff needs removed/stripped/zipped here?
    # Photoshop files inside a container ?
    rm -f /usr/lib/ceph/mgr/dashboard/static/AdminLTE-*/plugins/datatables/extensions/TableTools/images/psd/* && \
    # Some logfiles are not empty, there is no need to keep them
    find /var/log/ -type f -exec truncate -s 0 {} \; && \
    #
    #
    # Report size savings (strip / from end)
    FINAL_SIZE="$(bash -c 'sz="$(du -sm --exclude=/proc /)" ; echo "${sz%*/}"')" && \
    REMOVED_SIZE=$((INITIAL_SIZE - FINAL_SIZE)) && \
    echo "Cleaning process removed ${REMOVED_SIZE}MB" && \
    echo "Dropped container size from ${INITIAL_SIZE}MB to ${FINAL_SIZE}MB" && \
    #
    # Verify that the packages installed haven't been accidentally cleaned
    apt-cache show \
          sharutils \
          lsof \
           \
           \
          etcd-client \
          s3cmd && echo 'Packages verified successfully'

#======================================================
# Add ceph-container files
#======================================================

# Add s3cfg file
ADD s3cfg /root/.s3cfg

# Add templates for confd
ADD ./confd/templates/* /etc/confd/templates/
ADD ./confd/conf.d/* /etc/confd/conf.d/

# Add bootstrap script, ceph defaults key/values for KV store
ADD docker_scripts/*.sh docker_scripts/check_zombie_mons.py docker_scripts/osd_scenarios/* docker_scripts/entrypoint.sh.in docker_scripts/disabled_scenario /opt/ceph-container/bin/
ADD ceph.defaults /opt/ceph-container/etc/
# ADD *.sh ceph.defaults check_zombie_mons.py ./osd_scenarios/* entrypoint.sh.in disabled_scenario /

# Copye sree web interface for cn
# We use COPY instead of ADD for tarball so that it does not get extracted automatically at build time
COPY docker_scripts/Sree-0.2.tar.gz /opt/ceph-container/tmp/sree.tar.gz

# Modify the entrypoint
RUN bash "/opt/ceph-container/bin/generate_entrypoint.sh" && \
  rm -f /opt/ceph-container/bin/generate_entrypoint.sh && \
  bash -n /opt/ceph-container/bin/*.sh

# Execute the entrypoint
WORKDIR /
ENTRYPOINT ["/opt/ceph-container/bin/entrypoint.sh"]